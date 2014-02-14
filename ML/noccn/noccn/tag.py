import cPickle
from fnmatch import fnmatch
import operator
import os
import random
import traceback
import math
import csv
from multiprocessing import Process
from itertools import tee, izip_longest
import sys
import time

import numpy as np
from PIL import Image
from PIL import ImageOps
from joblib import Parallel
from joblib import delayed

from .ccn import convnet
from .ccn import options
from .script import get_sections
from .script import make_model
from .script import get_options
from .script import random_seed
from .script import resolve

# This is used to parse the xml files
import xml.etree.ElementTree as ET # can be speeded up using lxml possibly
import xml.dom.minidom as minidom

N_JOBS = -1
SIZE = (64, 64)

def _process_tag_item(size,channels,name):
    try:
	im = Image.open(name)
	im = ImageOps.fit(im, size, Image.ANTIALIAS)
	im_data = np.array(im)
	im_data = im_data.T.reshape(channels, -1).reshape(-1)
	im_data = im_data.astype(np.single)
        return im_data
    except:
        return None


def chunks(l, n):
    """ Yield successive n-sized chunks from l.
    """
    for i in xrange(0, len(l), n):
            yield l[i:i+n]


def get_next(some_iterable):
    it1, it2 = tee(iter(some_iterable))
    next(it2)
    return izip_longest(it1, it2)


class Tagger(object):
    def __init__(self, batch_size=1000, channels=3, size=SIZE, model=None, threshold=0.0, 
                 tagas=None, n_jobs=N_JOBS, more_meta=None, output_path=None, **kwargs):
        self.batch_size = batch_size
        self.channels = channels
        self.size = size
        self.n_jobs = n_jobs
        self.more_meta = more_meta or {}
        self.model = model
        self.threshold = threshold
        self.tagas = tagas
        self.output_path = output_path
        vars(self).update(**kwargs)  # O_o

    def __call__(self, all_names_and_labels, shuffle=False):
        batch_num = 1
        batch_means = np.zeros(((self.size[0]**2)*self.channels,1))
        self.count_correct = 0
        self.count_incorrect = 0
        start_time = time.clock()
        all_names_and_labels = ['/data2/leafbd/train/5941.xml']
        for names_and_labels,n_l_next in get_next(list(chunks(all_names_and_labels,self.batch_size))):
            loop_time = time.clock()
            if batch_num == 1:
		rows = Parallel(n_jobs=self.n_jobs)(
		    delayed(_process_tag_item)(self.size,self.channels,name)
		    for name, label in names_and_labels)
	    data = np.vstack([r for r in rows if r is not None]).T
            mean = data.mean(axis=1).reshape(((self.size[0]**2)*self.channels,1))
            data = data - mean
            self.model.start_predictions(data,self.threshold)
            if n_l_next is not None:
	        rows = Parallel(n_jobs=self.n_jobs)(
		    delayed(_process_tag_item)(self.size,self.channels,name)
		    for name, label in n_l_next)
            tags = self.model.finish_predictions()
            self.write_to_xml(zip(tags,names_and_labels))
            batch_num += 1
	    print "Tagged %d images in %.02f seconds" % (len(names_and_labels),time.clock()-loop_time)
            print 'Correct:' + `self.count_correct` + '\t\t\tIncorrect:',
            print `self.count_incorrect` + '\t\t\tRatio',
            print `float(self.count_correct)/(self.count_correct+self.count_incorrect)`
        print "Tagging complete. Tagged %d images in %.02f seconds" % (len(all_names_and_labels),time.clock()-start_time)
        print 'Correct:' + `self.count_correct` + '\t\t\tIncorrect:' + `self.count_incorrect` + '\t\t\tRatio' + `float(self.count_correct)/(self.count_correct+self.count_incorrect)`
        
    def prettify(self, elem):
	"""Return a pretty-printed XML string for the Element.
	"""
	rough_string = ET.tostring(elem, encoding='utf8', method='xml')
	reparsed = minidom.parseString(rough_string)
	return reparsed

    def write_to_xml(self,data):
        for tag,(name, label) in data:
            print tag
            print name
            '''
            tree = ET.parse(label)
            root = tree.getroot()
            actual = root.find('Content').text
            if self.tagas != None:
		title = ET.SubElement(root, self.tagas)
	 	title.text = tag
		tree.write(self.output_path+'/'+os.path.basename(label))
            if tag!='Exclude' and actual!='LeafScan':
                if tag == actual:
                    self.count_correct += 1
                else:
                    self.count_incorrect +=1
            '''


class TagConvNet(convnet.ConvNet):
    def __init__(self, op, load_dic, dp_params={}):
        convnet.ConvNet.__init__(self,op,load_dic,dp_params)
	self.softmax_idx = self.get_layer_idx('probs', check_type='softmax')
        self.tag_names = list(self.test_data_provider.batch_meta['label_names'])
        self.tag_names.append('Exclude')
        self.b_data = None
        self.b_labels = None
        self.b_preds = None
        self.b_threshold = None

    def start_predictions(self, data, threshold):
	# Run the batch through the model
	self.b_data = np.require(data, requirements='C')
	self.b_labels = np.zeros((1, data.shape[1]), dtype=np.single)
	self.b_preds = np.zeros((data.shape[1], len(self.tag_names)-1), dtype=np.single)
        self.b_threshold = threshold;
	self.libmodel.startFeatureWriter([self.b_data, self.b_labels, self.b_preds], self.softmax_idx)

    def finish_predictions(self):
	self.finish_batch()
        # Process the batch
        threshold_array = np.empty((self.b_preds.shape[0],1))
        threshold_array.fill(self.b_threshold)
        print self.b_preds
        self.b_preds = np.hstack((self.b_preds,threshold_array))
        return [self.tag_names[i] for i in np.nditer(self.b_preds.argmax(axis=1))]

    def write_predictions(self):
        pass

    def report(self):
        pass

    @classmethod
    def get_options_parser(cls):
        op = convnet.ConvNet.get_options_parser()
        for option in list(op.options):
            if option not in ('gpu', 'load_file'):
                op.delete_option(option)
        return op


def find(root, pattern):
    for path, folders, files in os.walk(root, followlinks=True):
        for fname in files:
            if fnmatch(fname, pattern):
                yield os.path.join(path, fname)


def _collect_filenames_and_labels(cfg):
    path = cfg['input-path']
    pattern = cfg.get('pattern', '*.jpg')
    metadata_file_ext = cfg.get('meta_data_file_ext', '.xml')
    filenames_and_labels = []
    counter = 0
    for fname in find(path, pattern):
        label = os.path.splitext(fname)[0] + metadata_file_ext
        filenames_and_labels.append((fname, label))
        counter += 1
    print 'Images found: ' + `counter`
    return np.array(filenames_and_labels)


def console():
    cfg = get_options(sys.argv[1], 'tag')
    cfg_dataset = get_options(sys.argv[1], 'dataset')
    random_seed(int(cfg.get('seed', '42')))
    collector = resolve(
        cfg.get('collector', 'noccn.tag._collect_filenames_and_labels'))
    filenames_and_labels = collector(cfg)
    creator = resolve(cfg.get('creator', 'noccn.tag.Tagger'))
    create = creator(
        batch_size=int(cfg.get('batch-size', 1000)),
        channels=int(cfg_dataset.get('channels', 3)),
        size=eval(cfg_dataset.get('size', '(64, 64)')),
        model=make_model(TagConvNet,'tag',sys.argv[1]),
        threshold=float(cfg.get('threshold','0.75')),
        tagas=cfg.get('tagas',None),
        output_path=cfg.get('output-path',None)
        )
    create(filenames_and_labels)
