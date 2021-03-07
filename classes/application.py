import os
import re
import sys
import csv
import time
import py7zr
from pathlib2 import Path

from dotenv import load_dotenv
from datetime import datetime
from classes.additional_code_parser import AdditionalCodeParser
from classes.commodity_parser import CommodityParser
from classes.footnote_parser import FootnoteParser
from classes.measure_parser import MeasureParser
from classes.appender import Appender
from classes.enums import CommonString
from classes.functions import functions as f

from classes_gen.database import Database
from classes_gen.footnote import Footnote
from classes_gen.commodity import Commodity
from classes_gen.measure import Measure
from classes_gen.measure_component import MeasureComponent
from classes_gen.measure_condition import MeasureCondition
from classes_gen.measure_type import MeasureType
from classes_gen.seasonal_rate import SeasonalRate
from classes_gen.supplementary_unit import SupplementaryUnit
from classes_gen.supplementary_unit import UnmatchedSupplementaryUnit
from classes_gen.geographical_area import GeographicalArea
from classes_gen.commodity_footnote import CommodityFootnote
from classes_gen.simplified_procedure_value import SimplifiedProcedureValue
from classes_gen.measure_excluded_geographical_area import MeasureExcludedGeographicalArea
from classes_gen.footnote_association_measure import FootnoteAssociationMeasure
from classes_gen.pr_measure import PrMeasure


class Application(object):
    def __init__(self):
        load_dotenv('.env')
        self.WRITE_MEASURES = int(os.getenv('WRITE_MEASURES'))
        self.WRITE_ADDITIONAL_CODES = int(os.getenv('WRITE_ADDITIONAL_CODES'))
        self.WRITE_FOOTNOTES = int(os.getenv('WRITE_FOOTNOTES'))

        d = datetime.now()
        self.SNAPSHOT_DATE = d.strftime('%Y-%m-%d')

        self.COMPARISON_DATE = datetime.strptime(
            os.getenv('COMPARISON_DATE'), '%Y-%m-%d')
        self.mfns = {}
        self.get_scope()
        self.get_folders()
        self.get_process_scope()

    def create_icl_vme(self):
        self.get_reference_data()
        self.get_footnotes()
        self.get_commodity_footnotes()
        self.open_extract()
        self.write_commodity_header()
        self.get_commodities()
        self.write_footnotes()
        self.close_extract()
        self.run_grep()
        self.zip_extract()
        self.zip_extract_csv()
        self.zip_extract_commodity_csv()

    def run_grep(self):
        print("Starting measure count")
        self.measure_count = 0
        self.measure_exception_count = 0
        with open(self.filepath) as fp:
            line = fp.readline()
            while line:
                if line[0:2] == "ME":
                    self.measure_count += 1
                elif line[0:2] == "MX":
                    self.measure_exception_count += 1

                line = fp.readline()
        fp.close()
        
        self.TOTAL_RECORD_COUNT = 0
        self.TOTAL_RECORD_COUNT += self.commodity_count
        self.TOTAL_RECORD_COUNT += self.additional_code_count
        self.TOTAL_RECORD_COUNT += self.measure_count
        self.TOTAL_RECORD_COUNT += self.measure_exception_count

        path = Path(self.filepath)
        text = path.read_text()
        text = text.replace("ME_RECORD_COUNT", str(
            self.measure_count).rjust(7, "0"))
        text = text.replace("MX_RECORD_COUNT", str(
            self.measure_exception_count).rjust(7, "0"))
        text = text.replace("TOTAL_RECORD_COUNT", str(
            self.TOTAL_RECORD_COUNT).rjust(11, "0"))
        path.write_text(text)
        print("Ending measure count")

        pass
        # grep -c "^ME" hmrc-tariff-ascii-05-mar-2021.txt
        # grep -c "^MX" hmrc-tariff-ascii-05-mar-2021.txt

    def get_scope(self):
        # Takes arguments from the command line to identify
        # whether to process UK or EU data
        if len(sys.argv) > 1:
            self.scope = sys.argv[1].lower()
        else:
            print("Please specify the country scope (uk or xi)")
            sys.exit()

        if self.scope not in ("uk", "xi"):
            print("Please specify the country scope (uk or xi)")
            sys.exit()

        load_dotenv('.env')
        if self.scope == "uk":
            self.DATABASE = os.getenv('DATABASE_UK')
        else:
            self.DATABASE = os.getenv('DATABASE_EU')

    def get_process_scope(self):
        # Takes arguments from the command line to identify
        # which commodities to process
        if len(sys.argv) > 2:
            self.start = int(sys.argv[2])
            if len(sys.argv) > 3:
                self.end = int(sys.argv[3])
            else:
                self.end = 10
        else:
            self.start = 0
            self.end = 10

    def get_commodities(self):
        # These need to be set at the start before any of the runs, and not reset by the runs
        self.commodity_count = 0
        self.additional_code_count = 0
        self.measure_count = 0
        self.measure_exception_count = 0

        for i in range(self.start, self.end):
            self.commodities = []
            tic = time.perf_counter()
            print("\nDEALING WITH COMMODITY CODES STARTING WITH " + str(i))
            self.get_measure_components(i)
            self.get_measure_conditions(i)
            self.get_measure_excluded_geographical_areas(i)
            self.get_footnote_association_measures(i)
            self.get_measures(i)
            self.categorise_and_sort_measures()
            self.assign_measure_components()
            self.assign_measure_conditions()
            self.assign_measure_excluded_geographical_areas()
            self.assign_footnote_association_measures()
            self.create_measure_duties()

            iteration = str(i) + "%"
            sql = "select * from utils.goods_nomenclature_export_new('" + \
                iteration + "', '" + self.SNAPSHOT_DATE + "') order by 2, 3"
            d = Database()
            rows = d.run_query(sql)
            for row in rows:
                commodity = Commodity()
                commodity.COMMODITY_CODE = row[1]
                if commodity.COMMODITY_CODE[0:2] not in ('98', '99'):
                    commodity.goods_nomenclature_sid = row[0]
                    commodity.productline_suffix = row[2]
                    commodity.validity_start_date = row[3]
                    commodity.validity_end_date = row[4]
                    commodity.COMMODITY_EDATE = self.YYYYMMDD(row[3])
                    commodity.COMMODITY_LDATE = self.YYYYMMDD(row[4])
                    commodity.description = row[5].replace('"', "'")
                    commodity.number_indents = int(row[6])
                    commodity.leaf = int(str(row[9]))
                    commodity.significant_digits = int(row[10])
                    commodity.determine_commodity_type()
                    commodity.get_amendment_status()
                    self.commodities.append(commodity)

            self.assign_measures()

            if self.WRITE_FOOTNOTES == 1:
                self.assign_commodity_footnotes()
            self.build_commodity_hierarchy()

            for commodity in self.commodities:
                commodity.apply_commodity_inheritance()
                commodity.sort_inherited_measures()
                commodity.get_additional_code_indicator()
                commodity.apply_seasonal_rates(self.seasonal_rates)
                commodity.get_end_use()
                commodity.get_supplementary_units(self.supplementary_units)
                commodity.get_spv(self.spvs)

            for commodity in self.commodities:
                commodity.create_extract_line()

            toc = time.perf_counter()
            self.write_commodities()
            print(f"Ran in {toc - tic:0.2f} seconds")

        self.write_commodity_footer()

    def categorise_and_sort_measures(self):
        # Used to set a priority precedence for the measures that appear in
        # the export file(s) - MFNs come first etc.
        priority_lookup = {
            "103": 0,
            "105": 1,
            "306": 2,
            "305": 3,
            "142": 4,
            "145": 4,
            "122": 5,
            "123": 5,
            "143": 5,
            "146": 5,
            "112": 6,
            "115": 6,
            "117": 6,
            "119": 6,
            "551": 7,
            "552": 7,
            "553": 7,
            "554": 7
        }
        for measure in self.measures:
            priority = 99
            try:
                priority = priority_lookup[measure.measure_type_id]
            except:
                priority = 99
            measure.priority = priority

        self.measures.sort(key=lambda x: x.geographical_area_id, reverse=False)
        self.measures.sort(key=lambda x: x.priority, reverse=False)

    def assign_measure_components(self):
        # Assign the measure components to thr measures
        # Very slow - could be improved
        print("Assigning measure components")
        measure_count = len(self.measures)
        start_pos = 0
        for measure_component in self.measure_components:
            for measure in self.measures:
                if measure.measure_sid == measure_component.measure_sid:
                    measure.measure_components.append(measure_component)
                    break

    def assign_measure_conditions(self):
        # This is used for working out if there is a chance that the heading is ex head
        # If there is a 'Y' condition, then this typically means that there are exclusions
        print("Assigning measure conditions")

        for measure_condition in self.measure_conditions:
            for measure in self.measures:
                if measure.measure_sid == measure_condition.measure_sid:
                    measure.measure_conditions.append(measure_condition)
                    break

        for measure_condition in self.measure_conditions_exemption:
            for measure in self.measures:
                if measure.measure_sid == measure_condition:
                    measure.CMDTY_MEASURE_EX_HEAD_IND = "Y"
                    break

        for measure_condition in self.measure_conditions_licence:
            for measure in self.measures:
                if measure.measure_sid == measure_condition:
                    measure.FREE_CIRC_DOTI_REQD_IND = "Y"
                    break

    def assign_measure_excluded_geographical_areas(self):
        # Assign measure exclusions to measures
        print("Assigning measure excluded geographical areas")
        for measure_excluded_geographical_area in self.measure_excluded_geographical_areas:
            for measure in self.measures:
                if measure.measure_sid == measure_excluded_geographical_area.measure_sid:
                    measure.measure_excluded_geographical_areas.append(
                        measure_excluded_geographical_area)
                    break

    def assign_footnote_association_measures(self):
        # Assign footnote_association_measures
        print("Assigning footnotes to measures")
        for footnote_association_measure in self.footnote_association_measures:
            for measure in self.measures:
                if measure.measure_sid == footnote_association_measure.measure_sid:
                    measure.footnote_association_measures.append(
                        footnote_association_measure)
                    break

        # Now create a string from the associations per measure.
        # Start by sorting the footnotes alphabetically
        for measure in self.measures:
            measure.footnote_association_measures.sort(
                key=lambda x: x.footnote_id, reverse=False)
            measure.footnote_association_measures.sort(
                key=lambda x: x.footnote_type_id, reverse=False)
            measure.footnote_string = ""
            for footnote_association_measure in measure.footnote_association_measures:
                measure.footnote_string += footnote_association_measure.footnote_type_id + \
                    footnote_association_measure.footnote_id + "|"
            measure.footnote_string = measure.footnote_string.strip("|")

    def create_measure_duties(self):
        print("Creating measure duties")
        for measure in self.measures:
            measure.create_measure_duties()
            measure.create_extract_line_per_geography()

    def assign_measures(self):
        # Assign measures to commodity codes
        print("Assigning measures")
        for measure in self.measures:
            for commodity in self.commodities:
                if commodity.productline_suffix == "80":
                    if measure.goods_nomenclature_item_id == commodity.COMMODITY_CODE:
                        commodity.measures.append(measure)
                        break

    def get_measure_conditions(self, iteration):
        # Get relevant measures conditions
        print("Getting measure conditions")
        self.measure_conditions = []
        self.measure_conditions_exemption = []
        self.measure_conditions_licence = []

        # First, get all measure conditions - these are needed to add to the CSV version of the file
        sql = """
        select mc.measure_condition_sid, mc.measure_sid, mc.condition_code, mc.component_sequence_number,
        mc.condition_duty_amount, mc.condition_monetary_unit_code, mc.condition_measurement_unit_code,
        mc.condition_measurement_unit_qualifier_code, mc.action_code, mc.certificate_type_code, mc.certificate_code 
        from measure_conditions mc, utils.materialized_measures_real_end_dates m
        where m.measure_sid = mc.measure_sid 
        and left(m.goods_nomenclature_item_id, """ + str(len(str(iteration))) + """) = '""" + str(iteration) + """'
        and (m.validity_end_date is null or m.validity_end_date > '""" + self.SNAPSHOT_DATE + """')
        order by mc.measure_sid, mc.condition_code, mc.component_sequence_number 
        """
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            mc = MeasureCondition()
            mc.measure_condition_sid = row[0]
            mc.measure_sid = row[1]
            mc.condition_code = row[2]
            mc.component_sequence_number = row[3]
            mc.condition_duty_amount = row[4]
            mc.condition_monetary_unit_code = row[5]
            mc.condition_measurement_unit_code = row[6]
            mc.condition_measurement_unit_qualifier_code = row[7]
            mc.action_code = row[8]
            mc.certificate_type_code = row[9]
            mc.certificate_code = row[10]
            self.measure_conditions.append(mc)

            if mc.certificate_type_code == "Y":
                # Second, get the exemption type records - these are needed to do ...
                self.measure_conditions_exemption.append(mc.measure_sid)
            elif mc.certificate_type_code in ('D', '9', 'A', 'C', 'D', 'H', 'I', 'L', 'N', 'U', 'Z'):
                # Get licence requirement
                self.measure_conditions_licence.append(mc.measure_sid)

        self.measure_conditions_exemption = list(
            set(self.measure_conditions_exemption))
        self.measure_conditions_licence = list(
            set(self.measure_conditions_licence))

    def get_measure_components(self, iteration):
        # Get measure components
        print("Getting measure components")
        self.measure_components = []
        sql = """select mc.measure_sid, mc.duty_expression_id, mc.duty_amount, mc.monetary_unit_code,
        mc.measurement_unit_code, mc.measurement_unit_qualifier_code, m.goods_nomenclature_item_id
        from measure_components mc, utils.materialized_measures_real_end_dates m
        where m.measure_sid = mc.measure_sid 
        and left(m.goods_nomenclature_item_id, """ + str(len(str(iteration))) + """) = '""" + str(iteration) + """'
        and (m.validity_end_date is null or m.validity_end_date > '""" + self.SNAPSHOT_DATE + """')
        order by m.goods_nomenclature_item_id, m.measure_sid, mc.duty_expression_id;"""
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            measure_component = MeasureComponent()
            measure_component.measure_sid = row[0]
            measure_component.duty_expression_id = row[1]
            measure_component.duty_amount = row[2]
            measure_component.monetary_unit_code = row[3]
            measure_component.measurement_unit_code = row[4]
            measure_component.measurement_unit_qualifier_code = row[5]
            measure_component.goods_nomenclature_item_id = row[6]
            measure_component.get_cts_component_definition()

            self.measure_components.append(measure_component)

    def get_measure_excluded_geographical_areas(self, iteration):
        # Get measure geo exclusions
        print("Getting measure excluded geographical areas")
        self.measure_excluded_geographical_areas = []
        sql = """select mega.measure_sid, mega.excluded_geographical_area, mega.geographical_area_sid 
        from measure_excluded_geographical_areas mega, utils.materialized_measures_real_end_dates m
        where m.measure_sid = mega.measure_sid 
        and left(m.goods_nomenclature_item_id, """ + str(len(str(iteration))) + """) = '""" + str(iteration) + """'
        and (m.validity_end_date is null or m.validity_end_date > '""" + self.SNAPSHOT_DATE + """')
        order by mega.measure_sid, mega.excluded_geographical_area;"""
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            measure_excluded_geographical_area = MeasureExcludedGeographicalArea()
            measure_excluded_geographical_area.measure_sid = row[0]
            measure_excluded_geographical_area.excluded_geographical_area = row[1]
            measure_excluded_geographical_area.geographical_area_sid = row[2]

            self.measure_excluded_geographical_areas.append(
                measure_excluded_geographical_area)

    def get_footnote_association_measures(self, iteration):
        # Get measure footnotes
        print("Getting measure footnotes")
        self.footnote_association_measures = []
        sql = """
        select m.measure_sid, fam.footnote_type_id, fam.footnote_id 
        from footnote_association_measures fam, utils.materialized_measures_real_end_dates m
        where m.measure_sid = fam.measure_sid 
        and left(m.goods_nomenclature_item_id, """ + str(len(str(iteration))) + """) = '""" + str(iteration) + """'
        and (m.validity_end_date is null or m.validity_end_date > '""" + self.SNAPSHOT_DATE + """')
        order by m.goods_nomenclature_item_id, m.measure_sid, fam.footnote_id, fam.footnote_id ;
        """
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            footnote_association_measure = FootnoteAssociationMeasure()
            footnote_association_measure.measure_sid = row[0]
            footnote_association_measure.footnote_type_id = row[1]
            footnote_association_measure.footnote_id = row[2]

            self.footnote_association_measures.append(
                footnote_association_measure)

    def get_measures(self, iteration):
        # Get measures
        print("Getting measures")
        self.measures = []
        sql = """select m.*, mt.measure_type_series_id,
        mt.measure_component_applicable_code, mt.trade_movement_code
        from utils.materialized_measures_real_end_dates m, measure_types mt
        where m.measure_type_id = mt.measure_type_id
        and left(goods_nomenclature_item_id, """ + str(len(str(iteration))) + """) = '""" + str(iteration) + """'
        and (m.validity_end_date is null or m.validity_end_date >= '""" + self.SNAPSHOT_DATE + """')
        order by goods_nomenclature_item_id, measure_type_id;"""

        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            measure = Measure()
            measure.measure_sid = row[0]
            measure.goods_nomenclature_item_id = row[1]
            measure.geographical_area_id = row[2]
            measure.measure_type_id = row[3]
            measure.measure_generating_regulation_id = row[4]
            measure.ordernumber = row[5]
            measure.reduction_indicator = row[6]
            measure.additional_code_type_id = row[7]
            measure.additional_code_id = row[8]
            measure.additional_code = row[9]
            measure.measure_generating_regulation_role = row[10]
            measure.justification_regulation_role = row[11]
            measure.justification_regulation_id = row[12]
            measure.stopped_flag = row[13]
            measure.geographical_area_sid = row[14]
            measure.goods_nomenclature_sid = row[15]
            measure.additional_code_sid = row[16]
            measure.validity_start_date = row[18]
            measure.validity_end_date = row[19]
            measure.operation_date = row[20]
            measure.measure_type_series_id = row[21]
            measure.measure_component_applicable_code = int(row[22])
            measure.trade_movement_code = row[23]
            measure.get_import_export()

            if measure.measure_sid == 20100524:
                a = 1

            measure.expand_raw_data(
                self.measure_types, self.geographical_areas)

            self.measures.append(measure)

    def rebase_chapters(self):
        # Reset the indent of chapters to -1, so that they are
        # omitted from the hierarchy string
        print("Rebasing chapters")
        for commodity in self.commodities:
            commodity.get_entity_type()

            # Do not rebase data for the CSV file
            commodity.number_indents_csv = commodity.number_indents

            # Rebase data for working out hierarchical inheritance
            if commodity.significant_digits == 2:
                commodity.number_indents = -1

    def build_commodity_hierarchy(self):
        # Builds the commodity hierarchy
        self.rebase_chapters()
        print("Building commodity hierarchy")
        commodity_count = len(self.commodities)
        for loop in range(0, commodity_count):
            commodity = self.commodities[loop]
            # if commodity.leaf == "1":
            if commodity.leaf == 1 or commodity.leaf == 0:
                current_indent = commodity.number_indents
                for loop2 in range(loop - 1, -1, -1):
                    commodity2 = self.commodities[loop2]
                    if commodity2.number_indents < current_indent:
                        commodity.hierarchy.append(commodity2)
                        current_indent = commodity2.number_indents
                    if commodity2.number_indents == -1:
                        break
                commodity.hierarchy.reverse()
                commodity.build_hierarchy_string()

    def write_commodities(self):
        # Write all commodities
        print("Writing commmodities")
        barred_series = ['E', 'F', 'G', 'H', 'K',
                         'L', 'M', "N", "O", "R", "S", "Z"]
        for commodity in self.commodities:
            commodity_string = ""
            commodity_string += CommonString.quote_char + \
                commodity.COMMODITY_CODE + CommonString.quote_char + ","
            commodity_string += CommonString.quote_char + \
                commodity.productline_suffix + CommonString.quote_char + ","
            commodity_string += commodity.validity_start_date.strftime(
                '%Y-%m-%d') + ","
            if commodity.validity_end_date is None:
                commodity_string += ","
            else:
                commodity_string += commodity.validity_end_date.strftime(
                    '%Y-%m-%d') + ","
            commodity_string += CommonString.quote_char + \
                commodity.description_csv + CommonString.quote_char + ","
            commodity_string += str(commodity.number_indents_csv) + ","
            commodity_string += CommonString.quote_char + \
                commodity.entity_type + CommonString.quote_char + ","
            commodity_string += CommonString.quote_char + \
                f.YN(commodity.leaf) + CommonString.quote_char

            self.commodity_file_csv.write(
                commodity_string + CommonString.line_feed)

            if commodity.leaf == 1 or (commodity.significant_digits == 8 and commodity.productline_suffix == "80"):
                self.commodity_count += 1
                self.extract_file.write(commodity.extract_line)
                if self.WRITE_ADDITIONAL_CODES == 1:
                    if commodity.additional_code_string != "":
                        self.additional_code_count += 1
                        self.extract_file.write(
                            commodity.additional_code_string)

                if self.WRITE_MEASURES == 1:
                    for measure in commodity.measures_inherited:
                        if measure.measure_type_series_id not in barred_series:
                            if measure.RECORD_TYPE == "MX":
                                self.measure_exception_count += 1  # measure.line_count
                            else:
                                self.measure_count += measure.line_count
                            self.extract_file.write(measure.extract_line)
                            if measure.extract_line_csv != "":
                                self.extract_file_csv.write(
                                    CommonString.quote_char + commodity.COMMODITY_CODE + CommonString.quote_char + ",")
                        self.extract_file_csv.write(measure.extract_line_csv)

                    self.pipe_pr_measures(commodity.COMMODITY_CODE)

    def pipe_pr_measures(self, commodity):
        has_found = False
        for pr_measure in self.pr_measures:
            if pr_measure.commodity == commodity:
                self.measure_count += 1
                self.extract_file.write(
                    pr_measure.line + CommonString.line_feed)
                has_found = True
            else:
                if has_found == True:
                    break

    def get_folders(self):
        self.current_folder = os.getcwd()
        self.data_folder = os.path.join(self.current_folder, "data")
        self.reference_folder = os.path.join(self.data_folder, "reference")
        self.data_in_folder = os.path.join(self.data_folder, "in")
        self.data_out_folder = os.path.join(self.data_folder, "out")
        self.export_folder = os.path.join(self.current_folder, "_export")

        # Make the date-specific folder
        date_time_obj = datetime.strptime(self.SNAPSHOT_DATE, '%Y-%m-%d')
        self.year = date_time_obj.strftime("%Y")
        self.month = date_time_obj.strftime("%b").lower()
        self.month2 = date_time_obj.strftime("%m").lower()
        self.day = date_time_obj.strftime("%d")

        date_folder = self.year + "-" + self.month2 + "-" + self.day
        self.dated_folder = os.path.join(self.export_folder, date_folder)
        os.makedirs(self.dated_folder, exist_ok=True)

        # Under the date-specific folder, also make a scope (UK/XI) folder
        self.scope_folder = os.path.join(self.dated_folder, self.scope)
        os.makedirs(self.scope_folder, exist_ok=True)

        # Finally, make the destination folders
        self.icl_vme_folder = os.path.join(self.scope_folder, "icl_vme")
        self.csv_folder = os.path.join(self.scope_folder, "csv")
        os.makedirs(self.icl_vme_folder, exist_ok=True)
        os.makedirs(self.csv_folder, exist_ok=True)

    def open_extract(self):
        if CommonString.divider == "|":
            self.filename = "hmrc-tariff-ascii-" + self.day + \
                "-" + self.month + "-" + self.year + "-piped.txt"
        else:
            self.filename = "hmrc-tariff-ascii-" + self.day + \
                "-" + self.month + "-" + self.year + ".txt"

        # Work out the path to the ICL VME extract
        self.filepath = os.path.join(self.icl_vme_folder, self.filename)
        self.extract_file = open(self.filepath, "w+")

        # Work out the path to the measures CSV extract
        self.filename_csv = self.filename.replace(".txt", ".csv")
        self.filename_csv = self.filename_csv.replace("ascii", "measures")
        self.filepath_csv = os.path.join(self.csv_folder, self.filename_csv)
        self.extract_file_csv = open(self.filepath_csv, "w+")
        self.extract_file_csv.write('"commodity__code","measure__sid","measure__type__id","measure__type__description","measure__additional_code__code",measure__additional_code__description,"measure__duty_expression","measure__effective_start_date","measure__effective_end_date","measure__reduction_indicator","measure__footnotes","measure__geographical_area__sid","measure__geographical_area__id","measure__geographical_area__description","measure__excluded_geographical_areas__ids","measure__quota__order_number"' + CommonString.line_feed)

        # Commodities CSV
        self.commodity_filename_csv = self.filename_csv.replace(
            "measures", "commodities")
        self.commodity_filepath_csv = os.path.join(
            self.csv_folder, self.commodity_filename_csv)
        self.commodity_file_csv = open(self.commodity_filepath_csv, "w+")
        self.commodity_file_csv.write(
            '"commodity__code","productline__suffix","start__date","end__date","description","indents","entity__type","end_line"' + CommonString.line_feed)

    def close_extract(self):
        self.extract_file.close()
        self.extract_file_csv.close()
        self.commodity_file_csv.close()

    def zip_extract(self):
        self.zipfile = self.filepath.replace(".txt", ".7z")
        try:
            os.remove(self.zipfile)
        except:
            pass
        with py7zr.SevenZipFile(self.zipfile, 'w') as archive:
            archive.write(self.filepath, self.filename)

    def zip_extract_csv(self):
        self.zipfile = self.filepath_csv.replace(".csv", ".7z")
        try:
            os.remove(self.zipfile)
        except:
            pass
        with py7zr.SevenZipFile(self.zipfile, 'w') as archive:
            archive.write(self.filepath_csv, self.filename_csv)

    def zip_extract_commodity_csv(self):
        self.zipfile = self.commodity_filepath_csv.replace(".csv", ".7z")
        try:
            os.remove(self.zipfile)
        except:
            pass
        with py7zr.SevenZipFile(self.zipfile, 'w') as archive:
            archive.write(self.commodity_filepath_csv,
                          self.commodity_filename_csv)

    def get_commodity_footnotes(self):
        print("Getting commodity-level footnote associations")
        self.commodity_footnotes = []
        self.commodities_with_footnotes = []
        sql = """select gn.goods_nomenclature_item_id, gn.goods_nomenclature_sid,
        fagn.footnote_type as footnote_type_id, fagn.footnote_id 
        from  footnote_association_goods_nomenclatures fagn, goods_nomenclatures gn 
        where  fagn.goods_nomenclature_sid = gn.goods_nomenclature_sid 
        and fagn.validity_end_date is null 
        and gn.validity_end_date is null
        order by gn.goods_nomenclature_item_id, fagn.footnote_type, fagn.footnote_id;"""
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            footnote = CommodityFootnote()
            footnote.goods_nomenclature_item_id = row[0]
            footnote.goods_nomenclature_sid = row[1]
            footnote.footnote_type_id = row[2]
            footnote.footnote_id = row[3]
            footnote.get_footnote_number()
            self.commodity_footnotes.append(footnote)
            self.commodities_with_footnotes.append(
                footnote.goods_nomenclature_sid)

        self.commodities_with_footnotes = set(self.commodities_with_footnotes)

    def assign_commodity_footnotes(self):
        print("Starting footnote assignment")
        for footnote_association in self.commodity_footnotes:
            for commodity in self.commodities:
                if footnote_association.goods_nomenclature_sid == commodity.goods_nomenclature_sid:
                    commodity.footnotes.append(footnote_association)
                    break

        for commodity in self.commodities:
            commodity.append_footnotes_to_description()

        print("Ending footnote assignment")

    def get_footnotes(self):
        # Problem with footnotes is the size of the fields
        # They are identified by 5 digits in the TAP / CDS feed
        # but only by three in the data from CHIEF - therefore we need a kludge massage:
        # The answer is
        # Any footnotes beginning with NC - add 800 to the IDs:
        # Any footnotes beginning with PN - add 900 to the IDs:
        # Any footnotes beginning with TN - as-is
        # This gets round the possible duplication on the three digits:

        print("Getting footnotes")
        self.footnotes = []
        sql = """SELECT f1.footnote_type_id,
        f1.footnote_id, fd1.description,
        f1.validity_start_date, f1.validity_end_date
        FROM footnote_descriptions fd1, footnotes f1, footnote_types ft 
        where f1.footnote_type_id = ft.footnote_type_id 
        and ft.application_code in (1, 2)
        and f1.validity_end_date is null
        and fd1.footnote_id::text = f1.footnote_id::text AND fd1.footnote_type_id::text = f1.footnote_type_id::text AND (fd1.footnote_description_period_sid IN ( SELECT max(ft2.footnote_description_period_sid) AS max
        FROM footnote_descriptions ft2
        WHERE fd1.footnote_type_id::text = ft2.footnote_type_id::text AND fd1.footnote_id::text = ft2.footnote_id::text))
        ORDER BY fd1.footnote_type_id, fd1.footnote_id;"""
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            footnote = Footnote()
            footnote.footnote_type_id = row[0]
            footnote.footnote_id = row[1]
            footnote.RECORD_TYPE = "FN"
            footnote.FOOTNOTE_EDATE = self.YYYYMMDD(row[3])
            footnote.FOOTNOTE_LDATE = self.YYYYMMDD(row[4])
            footnote.FOOTNOTE_TEXT = row[2]
            footnote.get_footnote_number()
            footnote.format_text()
            self.footnotes.append(footnote)

    def write_footnote_header(self):
        # HF2020121500000021001
        # RECORD-TYPE 2
        # DATE-CREATED 8
        # TIME-CREATED 6
        # RUN-NUMBER 5
        self.footnote_header = "HF"
        self.footnote_header += self.YYYYMMDD(datetime.now())
        self.footnote_header += self.HHMMSS(datetime.now())
        self.footnote_header += "21002"
        self.footnote_header += CommonString.line_feed
        self.extract_file.write(self.footnote_header)

    def write_footnote_footer(self):
        # CF0000120
        # RECORD-TYPE 2
        # FOOTNOTE-RECORD-COUNT 7
        self.footnote_footer = "CF"
        self.footnote_footer += str(len(self.footnotes)).zfill(7)
        self.extract_file.write(self.footnote_footer)

    def write_commodity_header(self):
        # HF2020121500000021001
        # RECORD-TYPE 2
        # DATE-CREATED 8
        # TIME-CREATED 6
        # RUN-NUMBER 5
        self.commodity_header = "HE"
        self.commodity_header += self.YYYYMMDD(datetime.now())
        self.commodity_header += self.HHMMSS(datetime.now())
        self.commodity_header += "21002"
        self.commodity_header += CommonString.line_feed
        self.extract_file.write(self.commodity_header)

    def write_commodity_footer(self):
        """
        CO0018494000128803435560000000000342100000366759
        CO
        0018494
        0001288
        0343556
        0000000
        0003421
        00000366759

        CM-RECORD-COUNT	7	9(7)
        CA-RECORD-COUNT	7	9(7)
        ME-RECORD-COUNT	7	9(7)
        MD-RECORD-COUNT	7	9(7)
        MX-RECORD-COUNT	7	9(7)
        TOTAL-RECORD-COUNT	11	9(11)
        """

        # self.measure_exception_count = 0
        total_record_count = self.commodity_count + self.additional_code_count + \
            self.measure_count + self.measure_exception_count

        self.CM_RECORD_COUNT = str(self.commodity_count).rjust(7, "0")
        self.CA_RECORD_COUNT = str(self.additional_code_count).rjust(7, "0")
        ME_RECORD_COUNT = str(self.measure_count).rjust(7, "0")
        self.MD_RECORD_COUNT = "0000000"
        self.MX_RECORD_COUNT = str(self.measure_exception_count).rjust(7, "0")
        self.TOTAL_RECORD_COUNT = str(total_record_count).rjust(11, "0")

        self.commodity_footer = "CO"
        self.commodity_footer += self.CM_RECORD_COUNT
        self.commodity_footer += self.CA_RECORD_COUNT
        self.commodity_footer += "ME_RECORD_COUNT"
        self.commodity_footer += self.MD_RECORD_COUNT
        self.commodity_footer += "MX_RECORD_COUNT"
        self.commodity_footer += "TOTAL_RECORD_COUNT" + CommonString.line_feed

        self.extract_file.write(self.commodity_footer)

    def write_footnotes(self):
        if self.WRITE_FOOTNOTES == 1:
            print("Writing footnotes")
            self.write_footnote_header()
            for footnote in self.footnotes:
                self.extract_file.write(footnote.extract_line)
            self.write_footnote_footer()

    def YYYYMMDD(self, d):
        if d is None:
            return "00000000"
        else:
            ret = d.strftime("%Y%m%d")
            return ret

    def HHMMSS(self, d):
        if d is None:
            return "00000000"
        else:
            ret = d.strftime("%H%M%S")
            return ret

    def parse(self):
        # Commodity code -> measure appender
        parser = Appender()
        parser.parse()
        parser.create_csv()
        sys.exit()

        # Do the additional codes
        parser = AdditionalCodeParser()
        parser.parse()
        parser.create_csv()

        # Do the comm codes
        parser = CommodityParser()
        parser.parse()
        parser.create_csv()

        # Do the additional codes
        parser = FootnoteParser()
        parser.parse()
        parser.create_csv()

        # Do the measures
        parser = MeasureParser("ME")
        parser.parse()
        parser.create_csv()

        # Do the measure exclusions
        parser = MeasureParser("MX")
        parser.parse()
        parser.create_csv()

    def get_reference_data(self):
        self.get_measure_types()
        self.get_measure_types_friendly()
        self.get_geographical_areas_friendly()
        self.get_additional_codes_friendly()
        self.get_seasonal_rates()
        self.get_unmatched_supplementary_units()
        self.get_spvs()
        self.get_geographical_areas()
        self.get_supplementary_units_reference()
        self.load_pr_measures()

    def load_pr_measures(self):
        print("Getting PR measures")
        self.pr_measures = []
        filename = os.path.join(self.reference_folder, "pr.measures.txt")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                pr_measure = PrMeasure(row[0], row[1])
                self.pr_measures.append(pr_measure)
        pass

    def get_measure_types(self):
        print("Getting measure types")
        self.measure_types = []
        filename = os.path.join(self.reference_folder, "measure_types.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                if len(row) >= 4:
                    measure_type = MeasureType(row[0], row[1], row[2], row[3])
                    self.measure_types.append(measure_type)

    def get_measure_types_friendly(self):
        sql = """select mt.measure_type_id, mtd.description 
        from measure_types mt, measure_type_descriptions mtd 
        where mt.measure_type_id = mtd.measure_type_id 
        and mt.validity_end_date is null 
        order by 1
        """
        self.measure_types_friendly = {}
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            self.measure_types_friendly[row[0]] = row[1]

    def get_additional_codes_friendly(self):
        sql = """SELECT acd1.additional_code_sid,
        acd1.additional_code_type_id::text || acd1.additional_code::text AS code,
        acd1.description
        FROM additional_code_descriptions acd1,
        additional_codes ac
        where ac.validity_end_date is null
        and ac.additional_code_sid = acd1.additional_code_sid AND (acd1.oid IN ( SELECT max(acd2.oid) AS max
        FROM additional_code_descriptions acd2
        WHERE acd1.additional_code_type_id::text = acd2.additional_code_type_id::text AND acd1.additional_code::text = acd2.additional_code::text))
        ORDER BY (acd1.additional_code_type_id::text || acd1.additional_code::text);
        """
        self.additional_codes_friendly = {}
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            self.additional_codes_friendly[row[0]] = row[2]

    def get_geographical_areas_friendly(self):
        sql = """SELECT g.geographical_area_sid,
        geo1.geographical_area_id,
        geo1.description
        FROM geographical_area_descriptions geo1,
        geographical_areas g
        WHERE g.geographical_area_id::text = geo1.geographical_area_id::text
        AND (geo1.geographical_area_description_period_sid IN ( SELECT max(geo2.geographical_area_description_period_sid) AS max
        FROM geographical_area_descriptions geo2
        WHERE geo1.geographical_area_id::text = geo2.geographical_area_id::text))
        and g.validity_end_date is null
        ORDER BY geo1.geographical_area_id;"""
        self.geographical_areas_friendly = {}
        d = Database()
        rows = d.run_query(sql)
        for row in rows:
            self.geographical_areas_friendly[row[0]] = row[2]

    def get_seasonal_rates(self):
        # Read the seasonal rates from the reference CSV and load to a global list
        print("Getting seasonal rates")
        self.seasonal_rates = []
        filename = os.path.join(self.reference_folder, "seasonal_rates.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                seasonal_rate = SeasonalRate(
                    row[0], row[1], row[2], row[3], row[4])
                self.seasonal_rates.append(seasonal_rate)

    def get_unmatched_supplementary_units(self):
        # Read the unmatched supplementary units from the reference CSV and load to a global list, needed for excise
        print("Getting unmatched supplementary units")
        self.unmatched_supplementary_units = []
        filename = os.path.join(self.reference_folder,
                                "unmatched_supplementary_units.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                unmatched_supplementary_unit = UnmatchedSupplementaryUnit(
                    row[0], row[1], row[2], row[4])
                self.unmatched_supplementary_units.append(
                    unmatched_supplementary_unit)

    def get_spvs(self):
        print("Getting SPVs")
        self.spvs = []
        filename = os.path.join(self.reference_folder, "spvs.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                spv = SimplifiedProcedureValue(row[0], row[1])
                self.spvs.append(spv)

    def get_supplementary_units_reference(self):
        print("Getting supplementary units")
        self.supplementary_units = []
        self.supplementary_unit_dict = {}
        filename = os.path.join(self.reference_folder,
                                "supplementary_units.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                supplementary_unit = SupplementaryUnit(row[0], row[1], row[2])
                self.supplementary_units.append(supplementary_unit)
                self.supplementary_unit_dict[row[0]+row[1]] = row[2]

    def get_geographical_areas(self):
        print("Getting geographical areas")
        self.geographical_areas = []
        filename = os.path.join(self.reference_folder,
                                "geographical_areas.csv")
        with open(filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            for row in csv_reader:
                if len(row) >= 3:
                    geographical_area = GeographicalArea(
                        row[0], row[1], row[2])
                    self.geographical_areas.append(geographical_area)
