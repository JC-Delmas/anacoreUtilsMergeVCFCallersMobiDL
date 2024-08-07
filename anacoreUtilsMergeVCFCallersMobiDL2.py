#!/mnt/Bioinfo/Softs/src/conda/Anaconda2-2019.07/envs/mobiDL/bin/python3

__author__ = 'Frederic Escudie'
__edit__ = 'David Baux'
__edit__ = 'Charles Van Goethem'
__edit__ = 'Jean-Charles Delmas'
__copyright__ = 'Copyright (C) 2019 IUCT-O'
__license__ = 'GNU General Public License'
__version__ = '1.1.2'
__email__ = 'escudie.frederic@iuct-oncopole.fr'
__status__ = 'prod'

import os
import re
import sys
import numpy
import logging
import argparse
import subprocess
import logging
import warnings
from anacore.vcf import VCFIO, HeaderInfoAttr, HeaderFormatAttr


########################################################################
#
# FUNCTIONS
#
########################################################################
def normalize_vcf(input_vcf, output_vcf): # JC : left align and to split multiallelic sites
    """
    Normalize a VCF file using bcftools.
    
    :param input_vcf: Path to the input VCF file.
    :param output_vcf: Path to the output normalized VCF file.
    """
    bcftools_path = "/usr/bin/bcftools"
    reference_genome = "/mnt/chu-ngs/refData/genome/hg19_no_chr/hg19.fa"
    cmd = f"{bcftools_path} norm -f {reference_genome} -m -both -o {output_vcf} -O z {input_vcf}"
    
    # Print the command that will be executed for debugging purposes
    #print(f"Running command: {cmd}")
    
    try:
        print(f"Starting normalization for file: {input_vcf}")
        subprocess.run(cmd, shell=True, check=True)
        print(f"Normalization complete for file: {input_vcf} -> {output_vcf}")
        return output_vcf
    except subprocess.CalledProcessError as e:
        # Log an error message if the normalization fails
        logging.error(f"Error normalizing VCF file {input_vcf}: {e}")
        print(f"Error during normalization: {e}")
        sys.exit(1)

def is_complex_variant(record): # JC
    """
    Determine if the variant is complex based on its REF and ALT fields.
    
    :param record: The VCF record.
    :type record: VCFRecord
    :return: True if the variant is complex, False otherwise.
    :rtype: bool
    """
    return len(record.ref) > 1 or any(len(alt) > 1 for alt in record.alt)

def update_rs_id(record): # JC : add rs at the ID column
    """Update the ID of the variant with rs value from INFO if available."""
    if record.id is None or record.id == ".":
        for key in record.info.keys():
            if key.startswith("rs"):
                record.id = key
                break
    # JC : handle cases where the ID is ".;rsXXXXXX"
    elif record.id.startswith(".;"):
        ids = record.id.split(";")
        for id_part in ids:
            if id_part.startswith("rs"):
                record.id = id_part
                break

def getNewHeaderAttr(args):
    """
    Return renamed and new VCFHeader elements for the merged VCF.

    :param args: The script's parameters.
    :type args: NameSpace
    :return: VCFHeader elements (filter, info, format, samples).
    :rtype: dict
    """
    final_filter = {}
    final_info = {
        "SRC": HeaderInfoAttr(
            "SRC", type="String", number=".", description="Variant callers where the variant is identified. Possible values: {}".format(
                {name: "s" + str(idx) for idx, name in enumerate(args.calling_sources)}
            )
        )
    }
    final_format = {
        "AD": HeaderFormatAttr("AD", type="Integer", number="A", description="Allele Depth"),
        "DP": HeaderFormatAttr("DP", type="Integer", number="1", description="Total Depth"),
        "ADSRC": HeaderFormatAttr("ADSRC", type="Integer", number=".", description="Allele Depth by source"),
        "DPSRC": HeaderFormatAttr("DPSRC", type="Integer", number=".", description="Total Depth by source")
    }
    final_samples = None
    for idx_in, curr_in in enumerate(args.inputs_variants):
        with VCFIO(curr_in) as FH_vcf:
            # Samples
            if final_samples is None:
                final_samples = FH_vcf.samples
            elif FH_vcf.samples != final_samples:
                raise Exception(
                    "The samples in VCF are not the same: {} in {} and {} in {}.".format(
                        final_samples,
                        args.inputs_variants[0],
                        FH_vcf.samples,
                        curr_in
                    )
                )
            # FILTER
            for tag, data in FH_vcf.filter.items():
                new_tag = tag
                if tag not in args.shared_filters:  # Rename filters not based on caller
                    new_tag = "s{}_{}".format(idx_in, tag)
                    data.id = new_tag
                    # added david to keep source, but in description field
                    data.description += ', {}'.format(args.calling_sources[idx_in])
                    # removed david as ,source in FILTER section is not VCF compliant (at least 4.2)
                    # data.source = args.calling_sources[idx_in]
                    # end removed
                final_filter[new_tag] = data
            # INFO
            for tag, data in FH_vcf.info.items():
                if tag == args.annotations_field:
                    if tag not in final_info or len(final_info[tag].description) < len(data.description):  # Manage merge between callers with 0 variants (and 0 annotations) and callers with variants
                        final_info[tag] = data
                else:
                    new_tag = "s{}_{}".format(idx_in, tag)
                    data.id = new_tag
                    # added david to keep source, but in description field
                    data.description += ', {}'.format(args.calling_sources[idx_in])
                    # removed david as ,source in INFO section is VCF compliant (at least 4.2) but rejected by GATK 3.8 used for combineVariant
                    # data.source = args.calling_sources[idx_in]
                    # end removed
                    final_info[new_tag] = data
            qual_tag = "s{}_VCQUAL".format(idx_in)
            # modified david to keep source, but in description field
            final_info[qual_tag] = HeaderInfoAttr(qual_tag, type="Float", number="1", description="The variant quality, {}".format(args.calling_sources[idx_in]))
            # removed david as ,source in INFO section is VCF compliant (at least 4.2) but rejected by GATK 3.8 used for combineVariant
            # final_info[qual_tag] = HeaderInfoAttr(qual_tag, type="Float", number="1", description="The variant quality", source=args.calling_sources[idx_in])
            # end removed
            # FORMAT
            for tag, data in FH_vcf.format.items():
                # Rename FORMAT
                # modif david 26/03/2021
                # for s0 or 1st occurence of format we want a double value with and without prefix
                if re.search(r'^[^s]', tag) and \
                         tag not in final_format:
                    data.description += ', {}'.format(args.calling_sources[idx_in])
                    final_format[tag] = HeaderFormatAttr(tag, type=data.type, number=data.number, description=data.description)
                # end modif
                # if tag in final_format:
                new_tag = "s{}_{}".format(idx_in, tag)
                data.id = new_tag
                if not re.search(rf'{args.calling_sources[idx_in]}', data.description):
                    data.description += ', {}'.format(args.calling_sources[idx_in])
                final_format[new_tag] = data
            # print(final_format)
    return {
        "filter": final_filter,
        "info": final_info,
        "format": final_format,
        "samples": final_samples
    }

def getMergedRecords(inputs_variants, calling_sources, annotations_field, shared_filters, prioritize):
    """
    Merge VCFRecords coming from several variant callers.

    :param inputs_variants: Pathes to the variants files.
    :type inputs_variants: list
    :param calling_sources: Names of the variants callers (in same order as inputs_variants).
    :type calling_sources: list
    :param annotations_field: Field used to store annotations.
    :type annotations_field: str
    :param shared_filters: Filters tags applying to the variant and independent of caller like filters on annotations. These filters are not renamed to add caller ID as suffix.
    :type shared_filters: set
    :param prioritize: Whether to prioritize complex variants over simple variants.
    :type prioritize: bool
    :return: Merged VCF records.
    :rtype: list
    """
    variant_by_name = {}
    error_log = open("error_records.log", "w")  # Log


    # JC : Redirect warnings to error log
    def custom_showwarning(message, category, filename, lineno, file=None, line=None):
        error_log.write(f"WARNING: {message}, {category.__name__}, {filename}, {lineno}\n")

    warnings.showwarning = custom_showwarning

    for idx_in, curr_in in enumerate(inputs_variants):
        curr_caller = calling_sources[idx_in]

        # JC : Normalize the VCF file to ensure multi-allelic sites are split.
        normalized_vcf = normalize_vcf(curr_in, f"{curr_in}.normalized.vcf.gz")
        
        with VCFIO(normalized_vcf) as FH_in:  # Use the normalized VCF file for further processing.
            log.info("Process {}".format(curr_caller))
            record_counter = 0  # 5 records printed per caller for debug use
            for record in FH_in:
                variant_name = record.getName()
                # Extract AD and DP
                support_by_spl = {}
                try:
                    for spl in FH_in.samples:
                        # JC : Special handling for FreeBayes format
                        if curr_caller.lower() == "freebayes":
                            gt = record.samples[spl]["GT"]
                            dp = record.samples[spl]["DP"]
                            ad = record.samples[spl]["AD"]
                            
                            if isinstance(dp, list):
                                dp = dp[0]  # Assuming first value is the one we need
                            if isinstance(ad, list):
                                ad = ad[0]  # SAme here

                            # JC : Print extracted values for validation and debug
                            #if record_counter < 5:
                                #print(f"FreeBayes - Variant: {variant_name}, Sample: {spl}, GT: {gt}, DP: {dp}, AD: {ad}")

                            support_by_spl[spl] = {
                                "AD": int(ad),
                                "DP": int(dp),
                                "GT": gt
                            }
                        elif curr_caller.lower() == "strelka2":
                            # Extract GT, DP, and AD from the sample fields
                            sample_values = record.samples[spl]
                            gt = sample_values["GT"]
                            # ONLY FOR STRELKA2 : use DP if available, otherwise fallback to DPI
                            dp = sample_values.get("DP", sample_values.get("DPI", None))
                            ad = sample_values["AD"]

                            if isinstance(dp, list):
                                dp = dp[0]  # Assuming first value is the one we need
                            if isinstance(ad, list):
                                ad = ad[0]  # Assuming first value is the one we need

                            # JC : Print extracted values for validation
                            #if record_counter < 5:
                                #print(f"Strelka2 - Variant: {variant_name}, Sample: {spl}, GT: {gt}, DP: {dp}, AD: {ad}")

                            support_by_spl[spl] = {
                                "AD": int(ad),
                                "DP": int(dp),
                                "GT": gt
                            }
                        else:
                            support_by_spl[spl] = {
                                "AD": record.getAltAD(spl)[0],
                                "DP": record.getDP(spl),
                                "GT": record.samples[spl]["GT"]
                            }
                    record_counter += 1
                except Exception as e:
                    error_log.write(f"Error processing {variant_name} in {curr_caller}: {str(e)}\n")
                    error_log.write(f"{curr_caller} - Error details: {record.samples[spl]}\n")
                    continue
		# JC : end of the modification
                # Rename filters
                if record.filter is not None:
                    new_filter = []
                    for tag in record.filter:
                        if tag != "PASS":
                            if tag in shared_filters:  # Rename filters not based on caller
                                new_filter.append(tag)
                            else:
                                new_filter.append("s{}_{}".format(idx_in, tag))
                    record.filter = new_filter
                # Rename INFO
                new_info = {}
                for key, val in record.info.items():
                    if key == annotations_field:
                        new_info[key] = val
                    else:
                        new_info["s{}_{}".format(idx_in, key)] = val
                record.info = new_info
                # Backup quality
                if record.qual is not None:
                    record.info["s{}_VCQUAL".format(idx_in)] = record.qual
                # Rename FORMAT
                # modif david 16/03/2021
                # for s0 or 1st occurence of format we want a double value with and without prefix
                if variant_name not in variant_by_name:
                    record.format = [curr_filter for curr_filter in record.format]
                    record.format += ["s{}_{}".format(idx_in, curr_filter) for curr_filter in record.format]
                else:
                    record.format = ["s{}_{}".format(idx_in, curr_filter) for curr_filter in record.format]
                # genuine record.format = ["s{}_{}".format(idx_in, curr_filter) for curr_filter in record.format]
                # end modif david
                for spl_name, spl_info in record.samples.items():
                    renamed_info = {}
                    for key, val in spl_info.items():
                        # Rename FORMAT
                        # modif david 16/03/2021
                        # for s0 or 1st occurence of format we want a double value with and without prefix
                        if key not in renamed_info:
                            renamed_info[key] = val
                        renamed_info["s{}_{}".format(idx_in, key)] = val
                        # genuine renamed_info["s{}_{}".format(idx_in, key)] = val
                        # end modif david
                    record.samples[spl_name] = renamed_info

                # Add to storage
                if variant_name not in variant_by_name:
                    if record.samples[spl_name]['GT'] != '0/0':
                        update_rs_id(record) # JC
                        variant_by_name[variant_name] = record
                        # Data source
                        record.info["SRC"] = [curr_caller]
                        # Quality
                        if idx_in != 0:
                            record.qual = None  # For consistency, the quality of the variant comes only from the first caller of the variant
                        # AD and DP by sample (from the first caller finding the variant: callers are in user order)
                        # david removed as it is placed before GT and gatk combinevariant complains
                        #record.format.insert(0, "ADSRC")
                        #record.format.insert(0, "DPSRC")
                        # end removed
                        # david removed as we already have them
                        # record.format.insert(0, "AD")
                        # record.format.insert(0, "DP")
                        # end removed
                        # david removed as now useless
                        # for spl_name, spl_data in record.samples.items():
                        # end removed
                            # david removed as we already have them
                            # spl_data["AD"] = [support_by_spl[spl_name]["AD"]]
                            # spl_data["DP"] = support_by_spl[spl_name]["DP"]
                            # end removed
                            # david removed as it is placed before GT and gatk combinevariant complains
                            # spl_data["ADSRC"] = [support_by_spl[spl_name]["AD"]]
                            # spl_data["DPSRC"] = [support_by_spl[spl_name]["DP"]]
                            # end removed
                else:
                    if record.samples[spl_name]['GT'] != '0/0':
                        prev_variant = variant_by_name[variant_name]
                        prev_variant.info["SRC"].append(curr_caller)
                        # JC : Prioritize complex variants when -p
                        if prioritize and is_complex_variant(record) and not is_complex_variant(prev_variant):
                            variant_by_name[variant_name] = record
                            prev_variant = record
                        # IDs
                        if record.id is not None:
                            prev_ids = prev_variant.id.split(";") if prev_variant.id else [] # JC : 
                            prev_ids.extend(record.id.split(";"))
                            prev_ids = sorted(list(set(prev_ids)))
                            prev_variant.id = ";".join(prev_ids)
                        # FILTERS
                        if record.filter is not None:
                            if prev_variant.filter is None:
                                prev_variant.filter = record.filter
                            else:
                                prev_variant.filter = list(set(prev_variant.filter) or set(record.filter))
                        # FORMAT
                        prev_variant.format.extend(record.format)
                        # INFO
                        prev_variant.info.update(record.info)
                        for spl_name, spl_data in prev_variant.samples.items():
                            spl_data.update(record.samples[spl_name])
                            # david removed as it is placed before GT and gatk combinevariant complains
                            # spl_data["ADSRC"].append(support_by_spl[spl_name]["AD"])
                            # spl_data["DPSRC"].append(support_by_spl[spl_name]["DP"])
                            # end removed
                        update_rs_id(prev_variant)

                        # Traitement des ID pour s'assurer que les ID sont correctement définis en tant que "rs..."
                        if prev_variant.id is None or prev_variant.id == ".":
                            for key, val in record.info.items():
                                if key.startswith('rs'):
                                    prev_variant.id = key
                                    break

    return variant_by_name.values()
    error_log.close() # JC

def logACVariance(variants, log):
    """
    Display in log the variance on allele counts (AD and AF) between callers.

    :param variants: Merged VCF records.
    :type variants: list
    :param log: Logger object.
    :type log: logging.Logger
    """
    diff_AD = []
    diff_AF = []
    nb_var = 0
    for record in variants:
        if len(record.info["SRC"]) > 1:
            nb_var += 1
            for spl_name, spl_data in record.samples.items():
                # AD
                retained_AD = spl_data["AD"][0]
                max_diff = 0
                for curr_AD in spl_data["ADSRC"][1:]:
                    max_diff = max(max_diff, abs(retained_AD - curr_AD))
                diff_AD.append(max_diff)
                # AF
                AF = [AD / DP for AD, DP in zip(spl_data["ADSRC"], spl_data["DPSRC"])]
                retained_AF = AF[0]
                max_diff = 0
                for curr_AF in AF[1:]:
                    max_diff = max(max_diff, abs(retained_AF - curr_AF))
                diff_AF.append(max_diff)
    # Log
    if nb_var == 0:
        log.info("Differences between retained AF and others callers (without missing): 0 common variants")
        log.info("Differences between retained AD and others callers (without missing): 0 common variants")
    else:
        log.info("Differences between retained AF and others callers (without missing): median={:.1%}, upper_quartile={:.1%}, 90_persentile={:.1%} and max={:.1%} on {} variants".format(
            numpy.percentile(diff_AF, 50, interpolation='midpoint'),
            numpy.percentile(diff_AF, 75, interpolation='midpoint'),
            numpy.percentile(diff_AF, 90, interpolation='midpoint'),
            max(diff_AF),
            nb_var
        ))
        log.info("Differences between retained AD and others callers (without missing): median={}, upper_quartile={}, 90_persentile={} and max={} on {} variants".format(
            int(numpy.percentile(diff_AD, 50, interpolation='midpoint')),
            int(numpy.percentile(diff_AD, 75, interpolation='midpoint')),
            int(numpy.percentile(diff_AD, 90, interpolation='midpoint')),
            max(diff_AD),
            nb_var
        ))


########################################################################
#
# MAIN
#
########################################################################
if __name__ == "__main__":
    # Manage parameters
    parser = argparse.ArgumentParser(description='Merge VCF coming from different calling on same sample(s). It is strongly recommended to apply this script after standardization and before annotation and filtering/tagging.')
    parser.add_argument('-a', '--annotations-field', default="ANN", help='Field used to store annotations. [Default: %(default)s]')
    parser.add_argument('-s', '--shared-filters', nargs='*', default=["lowAF", "OOT", "homoP", "popAF", "CSQ", "ANN.COLLOC", "ANN.RNA", "ANN.CSQ", "ANN.popAF"], help='Filters tags applying to the variant and independent of caller like filters on annotations. These filters are not renamed to add caller ID as suffix. [Default: %(default)s]')
    parser.add_argument('-c', '--calling-sources', required=True, nargs='+', help='Name of the source in same order of --inputs-variants.')
    parser.add_argument('-p', '--prioritize', action='store_true', help='Prioritize complex variants over simple variants at the same position.') # JC
    group_input = parser.add_argument_group('Inputs')  # Inputs
    group_input.add_argument('-i', '--inputs-variants', required=True, nargs='+', help='Path to the variants files coming from different callers (format: VCF). The order determine the which AF and AD are retained: the first caller where it is found in this list.')
    group_output = parser.add_argument_group('Outputs')  # Outputs
    group_input.add_argument('-o', '--output-variants', required=True, help='Path to the merged variants file (format: VCF).')
    args = parser.parse_args()
    args.shared_filters = set(args.shared_filters)

    # Logger
    logging.basicConfig(format='%(asctime)s -- [%(filename)s][pid:%(process)d][%(levelname)s] -- %(message)s')
    log = logging.getLogger(os.path.basename(__file__))
    log.setLevel(logging.INFO)
    log.info("Command: " + " ".join(sys.argv))

    # Get merged records
    variants = getMergedRecords(args.inputs_variants, args.calling_sources, args.annotations_field, args.shared_filters, args.prioritize)

    # david removed as it is requires ADSRC and removed
    # Log differences in AF and AD
    # logACVariance(variants, log)
    # end removed

    # Write
    with VCFIO(args.output_variants, "w") as FH_out:
        # Header
        new_header = getNewHeaderAttr(args)
        FH_out.samples = new_header["samples"]
        FH_out.info = new_header["info"]
        FH_out.format = new_header["format"]
        FH_out.filter = new_header["filter"]
        FH_out.writeHeader()
        # Records
        for record in sorted(variants, key=lambda record: (record.chrom, record.refStart(), record.refEnd())):
            if record.filter is not None and len(record.filter) == 0:
                record.filter = ["PASS"]
            FH_out.write(record)
    if os.path.getsize("error_records.log") > 0:
        print("There are some variants that require your attention. Please check error_records.log for warnings or errors that occurred during processing.") # JC
    log.info("End of job")