#!/usr/bin/env python
# encoding: utf-8

"""
muscle.py

Created by Brant Faircloth on 02 May 2010 12:10 PDT (-0700).
Copyright (c) 2010 Brant C. Faircloth. All rights reserved.
"""

import pdb
import sys
import os
import re
import tempfile
import subprocess
import numpy
from Bio import AlignIO, SeqIO
from Bio.Align import AlignInfo
from Bio.SeqRecord import SeqRecord
from Bio.Alphabet import IUPAC, Gapped
from Bio.Align.Generic import Alignment
from Bio.Align.Applications import MuscleCommandline



class Align(object):
    """docstring for Align"""
    def __init__(self, input):
        self.input = input
        self.alignment = None
        self.trimmed_alignment = None
        self.perfect_trimmed_alignment = None
    
    def _clean(self, outtemp):
        # cleanup temp file
        os.remove(outtemp)
        # cleanup input file
        os.remove(self.input)
    
    def _find_ends(self, forward=True):
        """determine the first (or last) position where all reads in an alignment 
        start/stop matching"""
        if forward:
            theRange = xrange(self.alignment.get_alignment_length())
        else:
            theRange = reversed(xrange(self.alignment.get_alignment_length()))
        for col in theRange:
            if '-' in self.alignment.get_column(col):
                pass
            else:
                break
        return col
    
    def _base_checker(self, bases, sequence, loc):
        """ensure that any trimming that occurs does not start beyong the
        end of the sequence being trimmed"""
        # deal with the case where we just want to measure out from the
        # middle of a particular sequence
        if len(loc) == 1:
            loc = (loc, loc)
        if not bases > len(sequence.seq[:loc[0]]) and \
            not bases > len(sequence.seq[loc[1]:]):
            return True
    
    def _record_formatter(self, temp):
        """return a string formatted as a biopython sequence record"""
        temp_record = SeqRecord(temp)
        temp_record.id = sequence.id
        temp_record.name = sequence.name
        temp_record.description = sequence.description
        return temp_record
    
    def _alignment_summary(self, alignment):
        """return summary data for an alignment object using the AlignInfo
        class from BioPython"""
        summary = AlignInfo.SummaryInfo(alignment)
        consensus = summary.dumb_consensus()
        return summary, consensus
    
    def _read(self, format):
        """read an alignment from the CLI - largely for testing purposes"""
        self.alignment = AlignIO.read(open(self.input,'rU'), format)
    
    def get_probe_location(self):
        '''Pull the probe sequence from an alignment object and determine its position
        within the read'''
        # probe at bottom => reverse order
        for record in self.alignment[::-1]:
            if record.id == 'probe':
                start = re.search('^-*', str(record.seq))
                end   = re.search('-*$', str(record.seq))
                # should be first record
                break
        # ooh, this seems so very backwards
        self.ploc = (start.end(), end.start(),)
    
    def run_alignment(self, clean = True, consensus = True):
        """Align, as originally written gets bogged down. Add communicate method 
        and move away from pipes for holding information (this has always been 
        problematic for me with multiprocessing).  Move to tempfile-based
        output."""
        # create results file
        fd, outtemp = tempfile.mkstemp(suffix='.align')
        os.close(fd)
        # run MUSCLE on the temp file
        cline = MuscleCommandline(input=self.input, out=outtemp)
        stdout, stderr = subprocess.Popen(str(cline),
                                 stderr=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 shell=True).communicate(None)
        self.alignment = AlignIO.read(open(outtemp,'rU'), "fasta", alphabet = Gapped(IUPAC.unambiguous_dna, "-"))
        # build a dumb consensus
        if consensus:
            self.alignment_summary, self.alignment_consensus = \
                self._alignment_summary(self.alignment)
        # cleanup temp files
        if clean:
            self._clean(outtemp)
    
    def running_average(self, window_size, threshold, proportion = 0.3, k=None, running_probe=False):
        # iterate across the columns of the alignment and determine presence
        # or absence of base-identity in the column
        differences = []
        members = len(self.alignment)
        if not running_probe:
            for column in xrange(self.alignment.get_alignment_length()):
                column_values = self.alignment.get_column(column)
                # get the count of different bases in a column (converting
                # it to a set gets only the unique values)
                column_list = list(column_values)
                # use proportional removal of gaps
                if column_list.count('-') <= int(round(proportion * members, 0)):
                    column_list = [i for i in column_list if i != '-']
                #pdb.set_trace()
                if len(set(column_list)) > 1:
                    differences.append(0)
                else:
                    differences.append(1)
        else:
            for column in xrange(self.alignment.get_alignment_length()):
                column_values = list(self.alignment.get_column(column)) 
                # drop the index of the probe from the column_values 
                del column_values[k]
                # get the count of different bases in a column (converting
                # it to a set gets only the unique values).
                #
                # no need to convert to a list here because it is already one
                if len(set(column_values)) > 1:
                    differences.append(0)
                else:
                    differences.append(1)
        differences = numpy.array(differences)
        weight = numpy.repeat(1.0, window_size)/window_size
        running_average = numpy.convolve(differences, weight)[window_size-1:-(window_size-1)]
        good = numpy.where(running_average >= threshold)[0]
        # remember to add window size onto end of trim
        try:
            start_clip, end_clip = good[0], good[-1] + window_size
        except IndexError:
            start_clip, end_clip = None, None
        return start_clip, end_clip
    
    def trim_alignment(self, method = 'edges', remove_probe = None, bases = None, consensus = True, window_size = 20, threshold = 0.5):
        """Trim the alignment"""
        if method == 'edges':
            # find edges of the alignment
            start   = self._find_ends(forward=True)
            end     = self._find_ends(forward=False)
        elif method == 'running':
            start, end = self.running_average(window_size, threshold)
        elif method == 'running-probe':
            # get position of probe
            for k,v in enumerate(self.alignment):
                if v.name == 'probe':
                    break
                else:
                    pass
            start, end = self.running_average(window_size, threshold, k, True)
        #pdb.set_trace()
        if method == 'notrim':
            self.trimmed_alignment = self.alignment
        else:
            # create a new alignment object to hold our alignment
            self.trimmed_alignment = Alignment(Gapped(IUPAC.ambiguous_dna, "-"))
            for sequence in self.alignment:
                # ignore the probe sequence we added
                if (method == 'edges' or method == 'running' or method == 'running-probe') and not remove_probe:
                    # it is totally retarded that biopython only gives us the option to
                    # pass the Alignment object a name and str(sequence).  Given this 
                    # level of retardation, we'll fudge and use their private method
                    if start >= 0 and end:
                        self.trimmed_alignment._records.append(sequence[start:end])
                    else:
                        self.trimmed_alignment = None
                        break
                elif method == 'static' and not remove_probe and bases:
                    # get middle of alignment and trim out from that - there's a
                    # weakness here in that we are not actually locating the probe
                    # region, we're just locating the middle of the alignment
                    mid_point = len(sequence)/2
                    if self._base_checker(bases, sequence, mid_point):
                        self.trimmed_alignment._records.append(
                            sequence[mid_point-bases:mid_point+bases]
                            )
                    else:
                        self.trimmed_alignment = None
                elif method == 'static' and not remove_probe and bases and self.ploc:
                    # get middle of alignment and trim out from that - there's a
                    # weakness here in that we are not actually locating the probe
                    # region, we're just locating the middle of the alignment
                    if self._base_checker(bases, sequence, self.ploc):
                        self.trimmed_alignment._records.append(
                            sequence[self.ploc[0]-bases:self.ploc[1]+bases]
                            )
                    else:
                        self.trimmed_alignment = None
                elif remove_probe and self.ploc:
                    # we have to drop to sequence level to add sequence slices
                    # where we basically slice around the probes location
                    temp = sequence.seq[:self.ploc[0]] + sequence.seq[self.ploc[1]:]
                    self.trimmed_alignment._records.append( \
                        self._record_formatter(temp)
                        )
                elif method == 'static' and remove_probe and bases and self.ploc:
                    if self._base_checker(bases, sequence, self.ploc):
                        temp = sequence.seq[self.ploc[0]-bases:self.ploc[0]] + \
                            sequence.seq[self.ploc[1]:self.ploc[1]+bases]
                        self.trimmed_alignment._records.append( \
                            self._record_formatter(temp)
                            )
                    else:
                        self.trimmed_alignment = None
        # build a dumb consensus
        if consensus and self.trimmed_alignment:
            self.trimmed_alignment_summary, self.trimmed_alignment_consensus = \
                self._alignment_summary(self.trimmed_alignment)
        if not self.trimmed_alignment:
            print "\tAlignment {0} dropped due to trimming".format(self.alignment._records[0].description.split('|')[1])
    
    def trim_ambiguous_bases(self):
        """snip ambiguous bases from a trimmed_alignment"""
        ambiguous_bases = []
        # do this by finaing all ambiguous bases and then snipping the largest
        # chunk with no ambiguous bases from the entire alignment
        if not self.trimmed_alignment:
            self.perfect_trimmed_alignment = self.trimmed_alignment
        else:
            for column in xrange(0, self.trimmed_alignment.get_alignment_length()):
                if 'N' in self.trimmed_alignment.get_column(column):
                    ambiguous_bases.append(column)
            maximum = 0
            maximum_pos = None
            #pdb.set_trace()
            if not ambiguous_bases:
                self.perfect_trimmed_alignment = self.trimmed_alignment
            if ambiguous_bases:
                # prepend and append the start and end of the sequence so consider
                # those chunks outside the stop and start of ambiguous base runs.
                ambiguous_bases.insert(0,0)
                ambiguous_bases.append(self.trimmed_alignment.get_alignment_length() - 1)
                # create a new alignment object to hold our alignment
                self.perfect_trimmed_alignment = \
                    Alignment(Gapped(IUPAC.unambiguous_dna, "-"))
                for pos in xrange(len(ambiguous_bases)):
                    if pos + 1 < len(ambiguous_bases):
                        difference = ambiguous_bases[pos + 1] - \
                            ambiguous_bases[pos]
                        if difference > maximum:
                            maximum = difference
                            maximum_pos = (pos, pos+1)
                    else:
                        pass
                # make sure we catch cases where there is not best block
                if maximum_pos:
                    for sequence in self.trimmed_alignment:
                        self.perfect_trimmed_alignment._records.append(
                            sequence[ambiguous_bases[maximum_pos[0]] + 1
                                :ambiguous_bases[maximum_pos[1]]]
                                )
                else:
                    self.perfect_trimmed_alignment = None

if __name__ == '__main__':
    #test_alignment = Align('/Users/bcf/git/brant/seqcap/Test/align/new/chrZ_8059.nex')
    test_alignment = Align('/Users/bcf/git/brant/seqcap/Test/align/amb_trim_concat/concat.nex')
    test_alignment._read('nexus')
    pdb.set_trace()
    test_alignment.trim_alignment(method='running')
    test_alignment.trim_ambiguous_bases()
    pdb.set_trace()