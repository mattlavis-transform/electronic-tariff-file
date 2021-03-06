-- DROP SCHEMA utils;

CREATE SCHEMA utils AUTHORIZATION postgres;


-- utils.measures_real_end_dates source

CREATE MATERIALIZED VIEW utils.measures_real_end_dates
TABLESPACE pg_default
AS SELECT m.measure_sid,
    m.goods_nomenclature_item_id,
    m.geographical_area_id,
    m.measure_type_id,
    m.measure_generating_regulation_id,
    m.ordernumber,
    m.reduction_indicator,
    m.additional_code_type_id,
    m.additional_code_id,
    m.additional_code_type_id || m.additional_code_id::text AS additional_code,
    m.measure_generating_regulation_role,
    m.justification_regulation_role,
    m.justification_regulation_id,
    m.stopped_flag,
    m.geographical_area_sid,
    m.goods_nomenclature_sid,
    m.additional_code_sid,
    m.export_refund_nomenclature_sid,
    to_char(m.validity_start_date, 'YYYY-MM-DD'::text) AS validity_start_date,
    LEAST(to_char(m.validity_end_date, 'YYYY-MM-DD'::text), to_char(r.validity_end_date, 'YYYY-MM-DD'::text), to_char(r.effective_end_date, 'YYYY-MM-DD'::text)) AS validity_end_date
   FROM measures m,
    base_regulations r
  WHERE m.measure_generating_regulation_id::text = r.base_regulation_id::text
UNION
 SELECT m.measure_sid,
    m.goods_nomenclature_item_id,
    m.geographical_area_id,
    m.measure_type_id,
    m.measure_generating_regulation_id,
    m.ordernumber,
    m.reduction_indicator,
    m.additional_code_type_id,
    m.additional_code_id,
    m.additional_code_type_id || m.additional_code_id::text AS additional_code,
    m.measure_generating_regulation_role,
    m.justification_regulation_role,
    m.justification_regulation_id,
    m.stopped_flag,
    m.geographical_area_sid,
    m.goods_nomenclature_sid,
    m.additional_code_sid,
    m.export_refund_nomenclature_sid,
    to_char(m.validity_start_date, 'YYYY-MM-DD'::text) AS validity_start_date,
    LEAST(to_char(m.validity_end_date, 'YYYY-MM-DD'::text), to_char(r.validity_end_date, 'YYYY-MM-DD'::text), to_char(r.effective_end_date, 'YYYY-MM-DD'::text)) AS validity_end_date
   FROM measures m,
    modification_regulations r
  WHERE m.measure_generating_regulation_id::text = r.modification_regulation_id::text
WITH DATA;


CREATE OR REPLACE FUNCTION utils.goods_nomenclature_export_new(pchapter text, key_date character varying)
 RETURNS TABLE(goods_nomenclature_sid integer, goods_nomenclature_item_id character varying, producline_suffix character varying, validity_start_date timestamp without time zone, validity_end_date timestamp without time zone, description text, number_indents integer, chapter text, node text, leaf text, significant_digits integer)
 LANGUAGE plpgsql
AS $function$

#variable_conflict use_column

DECLARE key_date2 date := key_date::date;

BEGIN

IF pchapter = '' THEN
pchapter = '%';
END IF;

/* temporary table contains results of query plus a placeholder column for leaf - defaulted to 0
node column has the significant digits used to find child nodes having the same significant digits.
The basic query retrieves all current (and future) nomenclature with indents and descriptions */

DROP TABLE IF EXISTS tmp_nomenclature;

CREATE TEMP TABLE tmp_nomenclature ON COMMIT DROP AS
SELECT gn.goods_nomenclature_sid, gn.goods_nomenclature_item_id, gn.producline_suffix, gn.validity_start_date, gn.validity_end_date, 
regexp_replace(gnd.description, E'[\\n\\r]+', ' ', 'g') as description,
gni.number_indents, 
left (gn.goods_nomenclature_item_id, 2) "chapter",
REGEXP_REPLACE (gn.goods_nomenclature_item_id, '(00)+$', '') AS "node",
'0' AS "leaf",
CASE
WHEN RIGHT(gn.goods_nomenclature_item_id, 8) = '00000000' THEN 2
WHEN RIGHT(gn.goods_nomenclature_item_id, 6) = '000000' THEN 4
WHEN RIGHT(gn.goods_nomenclature_item_id, 4) = '0000' THEN 6
WHEN RIGHT(gn.goods_nomenclature_item_id, 2) = '00' THEN 8
ELSE 10
END As significant_digits
FROM goods_nomenclatures gn
JOIN goods_nomenclature_descriptions gnd ON gnd.goods_nomenclature_sid = gn.goods_nomenclature_sid
JOIN goods_nomenclature_description_periods gndp ON gndp.goods_nomenclature_description_period_sid = gnd.goods_nomenclature_description_period_sid
JOIN goods_nomenclature_indents gni ON gni.goods_nomenclature_sid = gn.goods_nomenclature_sid

WHERE (gn.validity_end_date IS NULL OR gn.validity_end_date >= key_date2)
AND gn.goods_nomenclature_item_id LIKE pchapter
AND gndp.goods_nomenclature_description_period_sid IN
(
    SELECT MAX (gndp2.goods_nomenclature_description_period_sid)
    FROM goods_nomenclature_description_periods gndp2
    WHERE gndp2.goods_nomenclature_sid = gnd.goods_nomenclature_sid
    AND gndp2.validity_start_date <= key_date2
)
AND gni.goods_nomenclature_indent_sid IN
(
    SELECT MAX (gni2.goods_nomenclature_indent_sid)
    FROM goods_nomenclature_indents gni2
    WHERE gni2.goods_nomenclature_sid = gn.goods_nomenclature_sid
    AND gni2.validity_start_date <= key_date2
);



/* Index to speed up child node matching - need to perf test to see if any use */
CREATE INDEX t1_i_nomenclature 
ON tmp_nomenclature (goods_nomenclature_sid, goods_nomenclature_item_id);

/* Cursor loops through result set to identify if nodes are leaf and updates the flag if so */
declare cur_nomenclature CURSOR FOR SELECT * FROM tmp_nomenclature;

BEGIN

	FOR nom_record IN cur_nomenclature LOOP
		Raise Notice 'goods nomenclature item id %', nom_record.goods_nomenclature_item_id;
		
		/* Leaf nodes have to have pls of 80 and no children having the same nomenclature code */
		IF nom_record.producline_suffix = '80' THEN
			IF LENGTH (nom_record.node) = 10 OR NOT EXISTS (SELECT 1 
			FROM tmp_nomenclature 
			WHERE goods_nomenclature_item_id LIKE CONCAT(nom_record.node,'%')
			AND goods_nomenclature_item_id <> nom_record.goods_nomenclature_item_id) THEN
			
				UPDATE tmp_nomenclature tn
				SET leaf = '1'
				WHERE goods_nomenclature_sid = nom_record.goods_nomenclature_sid;
			
			END IF;
		END IF;
	
	END LOOP;

END;

RETURN QUERY 
SELECT * FROM tmp_nomenclature;

END;

$function$
;
