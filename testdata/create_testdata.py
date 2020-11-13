#!/usr/bin/env python
"""Create testdata suitable to run bfq pipelines by subsampling an existing bfq output folder.
"""
import sys
import os
import collections
import argparse
import warnings
import subprocess
import logging
import glob
import re
import random
import shutil
import logging

import pandas as pd

from configmaker import PIPELINE_MAP


def sample_samplesheet(input_fn, output_fn, samples, valid_samples):
    """Subset SampleSheet.csv 

    Subset by extracting lines under [Data] corresponding to samples.
    
    Parameters
    ----------
    input_fn : str
        Input filename
    output_fn : str
        Output filename
    samples : list-like
        List of sample-ids for subsetting
    valid_samples : list-like
        List of valid sample-ids
    
    """
    output = []
    remove_samples = set(valid_samples).difference(samples)
    with open(input_fn) as fh:
        for i, line in enumerate(fh):
            if line.startswith('[Data]'):
                header = fh.readline().strip('\n').split(',')
                sample_id_index = header.index('Sample_ID')
                header_line = i +1
    with open(input_fn) as fh:
        lines = fh.read().splitlines()
        for i, line in enumerate(lines):
            els = line.split(',')
            if i > header_line and els[sample_id_index] in remove_samples:
                pass
            else:
               output.append(line)
    with open(output_fn, 'w') as fh:
        for line in output:
            fh.write(line + '\n')


class BFQoutput():
    """Class representing a bfq-pipeline output directory

    Attributes
    ----------
    dirname : str 
       path to bfq-pipeline output directory
    pipeline : str 
       name of gcf-pipeline, can be None
    fastq_files : dict
       samples to fastq file mapping
 
    """
    def __init__(self, dirname=None):
        self.dirname = dirname
        self.pipeline = None
        self.fastq_files = None
        self._archive = None
        self._gcf_number = None
        self._fastq_dir = None

        self._inspect()

    def _inspect(self):
        """Validate bfq output directory
        """
        if not os.path.exists(self.dirname):
            raise ValueError('BFQ output dir does not exist')

        samplesheet = os.path.join(self.dirname, 'SampleSheet.csv')
        with open(samplesheet) as fh:
            for line in fh.read().splitlines():
                if line.startswith('ExperimentName'):
                    self._gcf_number = line.split(',')[1]
                    logging.info("identifed gcf number from samplesheet: {}".format(self._gcf_number))
                if line.startswith('Libprep'):
                    libprep = line.split(',')[1]
                    if not libprep in PIPELINE_MAP:
                        warnings.warn('failed to identify pipeline from libprep name, using `NA`')
                    self.pipeline = PIPELINE_MAP.get(libprep, None)
                    logging.info("identifed library prep kit from samplesheet: {}".format(libprep))
                    logging.info("pipeline: {}".format(self.pipeline))
                    break
        if self.pipeline == 'microbiome':
            self._fastq_dir = os.path.join(self.dirname, 'raw_fastq_{}'.format(self._gcf_number))
        else:
            self._fastq_dir = os.path.join(self.dirname, self._gcf_number)
        if not os.path.exists(self._fastq_dir):
            raise ValueError('Missing fastq dir. Expected {}'.format(self._fastq_dir)) 

        df = pd.read_csv(os.path.join(self.dirname, '{}_samplesheet.tsv'.format(self._gcf_number)), sep='\t')
        self.fastq_files = {}
        for sample in df.Sample_ID:
            self.fastq_files[sample] = glob.glob1(self._fastq_dir, '{}*.fastq.gz'.format(sample))
                
    def sample(self, output_dir, overwrite=True, n_reads=10000, n_samples=3, samples=None):
        """Main subsampling routine.
        This method subsamples and writes output files.

        Params
        ------
        output_dir : str, path-like
        overwrite : boolean
            Force overwriting of existing output directory
        n_reads : int
            Number of random subsampled reads for each fastq file
        n_samples : int
            Number of random subsampled samples. This will be ignored if `samples` is not None
        samples : list
            List of sample-ids to use in sampling
        """
        logging.info("start copy of files from bfq output to : {}".format(output_dir))
        if os.path.exists(output_dir):
            if overwrite:
                shutil.rmtree(output_dir)
            else:
                raise ValueError('Output dir already exists.')
        os.makedirs(output_dir, exist_ok=False)
        dirs = ['Stats', 'InterOp']
        files = ['bcl.done', 'Sample-Submission-Form.xlsx']
        os.makedirs(output_dir, exist_ok=True)
        for direc in dirs:
            src = os.path.join(self.dirname, direc)
            dst = os.path.join(output_dir, direc)
            logging.info("copy tree: {} -> {}".format(src, dst))
            shutil.copytree(src, dst)
        for fn in files:
            src = os.path.join(self.dirname, fn)
            dst = os.path.join(output_dir, fn)
            logging.info("copy file: {} -> {}".format(src, dst))
            shutil.copy(src, dst)

        # susbet and copy fastq files
        fastq_dir_output = os.path.join(output_dir, os.path.basename(self._fastq_dir))
        os.makedirs(fastq_dir_output, exist_ok=True)
        if samples is None:
            SAMPLES = list(self.fastq_files.keys())
            SAMPLES = random.choices(SAMPLES, k=n_samples)
        else:
            SAMPLES = [i.strip() for i in samples.split(',')]
            for i in SAMPLES:
                if i not in self.fastq_files.keys():
                    logging.warning('sample {} is not a vaid sample id'.format(i))
                    logging.warning('valid samples: {}'.format(str(self.fastq_files.keys())))
                    raise AssertionError
        for sample in SAMPLES:
            fq_files = self.fastq_files[sample]
            for fq_basename in fq_files:
                src = os.path.join(self._fastq_dir, fq_basename)
                if self.pipeline == 'single-cell':
                    new_baseneame = fq_basename
                else:
                    read_num = 'R1' if fq_basename.endswith('_R1.fastq') else 'R2'
                    new_basename = '{}_L001_{}_001.fastq'.format(sample, read_num)
                    dst = os.path.join(fastq_dir_output, new_basename)
                    cmd = 'zcat {} | seqkit sample -n {} -s 123456 > {}'.format(src, n_reads, dst)
                    logging.info(cmd)
                    subprocess.call(cmd, shell=True)
        # gzip fastq files         
        cmd = 'gzip {}/*'.format(fastq_dir_output)
        logging.info('compressing fastq files ....')
        logging.info(cmd)
        subprocess.call(cmd, shell=True)

        # subsample samplesheet
        logging.info('subsampling SampleSheet.csv ... ')
        src = os.path.join(self.dirname, 'SampleSheet.csv')
        dst = os.path.join(output_dir, 'SampleSheet.csv')
        sample_samplesheet(src, dst, samples=SAMPLES, valid_samples=list(self.fastq_files.keys()))

def create_argparser():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("runfolder", help="Path to flowcell dir")
    parser.add_argument("--output", help="Output dir")
    parser.add_argument("--n-reads", default=1000, type=int, help="Number of reads. (random subset)")
    parser.add_argument("--n-samples", default=3, type=int, help="Number of samples (random subset)")
    parser.add_argument("--samples", help="Comma separated list of sample ids to subset. This will overrride `--n-samples`")
    parser.add_argument("--verbose", action='store_true')
    
    return parser

def subset_fastq(fastq_files, output_dir, n=1000, seed=123456):
    """Sample random reads from fastq files using seqkit.

    Note: SeqKit - Ultrafast FASTA/Q kit needs to be installed.
    """
    for fq_name in fastq_files:
        basename = os.path.basename(fq_name)
        output_fn = os.path.join(output_dir, basename)
        cmd = 'zcat {} | seqkit sample -n {} -s {} > {}'.format(fq_name, n, seed, output_fn)
        os.system(cmd)
        logging.info('sampling fastq using seqkit ...')
        logging.info(cmd)

if __name__ == "__main__":
    parser = create_argparser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level='INFO', format='[%(levelname)s] %(message)s')

    bfq =  BFQoutput(args.runfolder)
    bfq.sample(args.output, n_reads=args.n_reads, n_samples=args.n_samples, samples=args.samples)

    
