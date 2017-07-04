#!/usr/bin/env python3
"""
Post-process Centrifuge/Kraken report files for Krona.
"""
# pylint: disable=no-name-in-module, not-an-iterable
import argparse
import multiprocessing as mp
import os
import platform
import sys
from typing import Counter, List, Dict, Set

from recentrifuge.centrifuge import process_report
from recentrifuge.config import Filename, Sample, TaxId
from recentrifuge.config import NODESFILE, NAMESFILE, HTML_SUFFIX, DEFMINTAXA
from recentrifuge.core import Taxonomy, TaxLevels, TaxTree, MultiTree, Rank
from recentrifuge.core import process_rank
from recentrifuge.krona import KronaTree, krona_from_xml

__version__ = '0.8.0'
__author__ = 'Jose Manuel Marti'
__date__ = 'Jul 2017'


def main():
    """Main entry point to recentrifuge."""
    # Argument Parser Configuration
    parser = argparse.ArgumentParser(
        description='Post-process Centrifuge/Kraken output',
        epilog=f'%(prog)s  - {__author__} - {__date__}'
    )
    parser.add_argument(
        '-V', '--version',
        action='version',
        version=f'%(prog)s release {__version__} ({__date__})'
    )
    parser.add_argument(
        '-f', '--filerep',
        action='append',
        metavar='FILE',
        required=True,
        help=('Centrifuge/Kraken report files ' +
              '(multiple -f is available to include several samples in plot')
    )
    parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='increase output verbosity'
    )
    parser.add_argument(
        '-n', '--nodespath',
        action='store',
        metavar='PATH',
        default='./',
        help=('path for the nodes information files (nodes.dmp and names.dmp' +
              ' from NCBI')
    )
    parser.add_argument(
        '-m', '--mintaxa',
        action='store',
        metavar='INT',
        default=DEFMINTAXA,
        help=('minimum taxa to avoid collapsing one level to the parent one ' +
              ('(%i per default)' % DEFMINTAXA))
    )
    parser.add_argument(
        '-k', '--nokollapse',
        action='store_true',
        help='Show the "cellular organisms" taxon (collapsed by default)'
    )
    parser.add_argument(
        '-o', '--outhtml',
        action='store',
        metavar='FILE',
        default=None,
        help='HTML Krona file (default: name inferred from report files)'
    )
    parser.add_argument(
        '-i', '--include',
        action='append',
        metavar='TAXID',
        type=TaxId,
        default=[],
        help=('NCBI taxid code to include a taxon and all underneath ' +
              '(multiple -i is available to include several taxid). ' +
              'By default all the taxa is considered for inclusion.')
    )
    parser.add_argument(
        '-x', '--exclude',
        action='append',
        metavar='TAXID',
        type=TaxId,
        default=[],
        help=('NCBI taxid code to exclude a taxon and all underneath ' +
              '(multiple -x is available to exclude several taxid)')
    )
    parser.add_argument(
        '-s', '--sequential',
        action='store_true',
        help='Deactivate parallel processing.'
    )
    parser.add_argument(
        '-a', '--avoidcross',
        action='store_true',
        help='Avoid cross analysis.'
    )
    parser.add_argument(
        '-c', '--control',
        action='store_true',
        help='Take the first sample as negative control.'
    )

    # Parse arguments
    args = parser.parse_args()
    reports = args.filerep
    verb = args.verbose
    nodesfile = os.path.join(args.nodespath, NODESFILE)
    namesfile = os.path.join(args.nodespath, NAMESFILE)
    mintaxa = int(args.mintaxa)
    collapse = not args.nokollapse
    excluding: Set[TaxId] = set(args.exclude)
    including: Set[TaxId] = set(args.include)
    sequential = args.sequential
    avoidcross = args.avoidcross
    control = args.control
    htmlfile: Filename = args.outhtml
    if not htmlfile:
        htmlfile = reports[0].split('_mhl')[0] + HTML_SUFFIX

    # Program header and chdir
    print(f'\n=-= {sys.argv[0]} =-= v{__version__} =-= {__date__} =-=\n')
    sys.stdout.flush()

    # Load NCBI nodes, names and build children
    ncbi: Taxonomy = Taxonomy(nodesfile, namesfile,
                              collapse, excluding, including)

    # Declare variables that will hold results for the samples analyzed
    trees: Dict[Sample, TaxTree] = {}
    abundances: Dict[Sample, Counter[TaxId]] = {}
    accs: Dict[Sample, Counter[TaxId]] = {}
    taxids: Dict[Sample, TaxLevels] = {}
    samples: List[Sample] = []
    #
    # Processing of report files in parallel
    #
    print('\033[90mPlease, wait, processing files in parallel...\033[0m\n')
    kwargs = {'taxonomy': ncbi, 'mintaxa': mintaxa, 'verb': verb}
    # Enable parallelization with 'spawn' under known platforms
    if platform.system() and not sequential:  # Only for known platforms
        mpctx = mp.get_context('spawn')  # Important for OSX&Win
        with mpctx.Pool(processes=min(os.cpu_count(),
                                      len(reports))) as pool:
            async_results = [pool.apply_async(
                process_report,
                args=[filerep],
                kwds=kwargs
            ) for filerep in reports]
            for report, (sample, trees[sample],
                         taxids[sample], abundances[sample],
                         accs[sample]) in zip(reports, [r.get() for r in
                                                        async_results]):
                samples.append(sample)
    else:  # sequential processing of each sample
        for report in reports:
            (sample, trees[sample],
             taxids[sample], abundances[sample],
             accs[sample]) = process_report(report, **kwargs)
            samples.append(sample)
    #
    # Cross analysis of samples in parallel by taxlevel
    #
    # Avoid if just a single report file of explicitly stated by flag
    if len(reports) > 1 and not avoidcross:
        print('\033[90mPlease, wait. ' +
              'Performing cross analysis in parallel...\033[0m\n')
        kwargs.update({'trees': trees, 'taxids': taxids,
                       'abundances': abundances, 'reports': reports,
                       'control': control})
        if platform.system() and not sequential:  # Only for known platforms
            mpctx = mp.get_context('spawn')  # Important for OSX&Win
            with mpctx.Pool(processes=min(os.cpu_count(), len(
                    Rank.selected_ranks))) as pool:
                async_results = [pool.apply_async(
                    process_rank,
                    args=[level],
                    kwds=kwargs
                ) for level in Rank.selected_ranks]
                for level, (filenames, abunds, accumulators) in zip(
                        Rank.selected_ranks,
                        [r.get() for r in async_results]):
                    samples.extend(filenames)
                    abundances.update(abunds)
                    accs.update(accumulators)
        else:  # sequential processing of each selected rank
            for level in Rank.selected_ranks:
                (filenames, abunds, accumulators) = process_rank(level,
                                                                 **kwargs)
                samples.extend(filenames)
                abundances.update(abunds)
                accs.update(accumulators)
    #
    # Generate Krona plot with all the results via Krona 2.0 XML spec
    #
    print('\033[90mBuilding the taxonomy multiple tree...\033[0m', end='')
    krona: KronaTree = KronaTree(samples)
    polytree: MultiTree = MultiTree(samples=samples)
    polytree.grow(taxonomy=ncbi, abundances=abundances, accs=accs)
    print('\033[92m OK! \033[0m')
    print('\033[90mGenerating Krona XML file...\033[0m', end='')
    polytree.toxml(taxonomy=ncbi, krona=krona)
    xmlfile: Filename = Filename(htmlfile + '.xml')
    krona.tofile(xmlfile)
    print('\033[92m OK! \033[0m')
    print('\033[90mGenerating final Krona plot...\033[0m')
    krona_from_xml(xmlfile, htmlfile)


if __name__ == '__main__':
    main()
