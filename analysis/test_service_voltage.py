"""Synthetic safety and statistical contracts for the private voltage snapshot."""
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import service_voltage as study


def frame(times, **values):
    return pd.DataFrame({'ts': pd.to_datetime(times, utc=True), **values})


def snapshot(root):
    """One synthetic local date; checks end-to-end I/O without the private archive."""
    start=pd.Timestamp('2026-01-01T00:00:00-08:00')
    end=pd.Timestamp('2026-01-02T00:00:00-08:00')
    times=pd.date_range(start.tz_convert('UTC'),end.tz_convert('UTC'),freq='5min',inclusive='left')
    meter=pd.DataFrame({'ts':times,'line_v':240.,'leg1_v':120.,'leg2_v':120.,'leg1_a':2.,'leg2_a':2.,'freq':60.})
    mirror=meter.drop(columns=['leg1_a','leg2_a']).copy()
    mirror['gen_w']=1000.;mirror['con_w']=1500.;mirror['voltage_semantics_valid']=1
    eagle=pd.DataFrame({'ts':pd.date_range(start.tz_convert('UTC'),end.tz_convert('UTC'),freq='min',inclusive='left'),'demand_w':500.})
    generation=pd.DataFrame({'ts':times,'src_ts':times,'gen_w':1000.,'con_w':1500.})
    weather=pd.DataFrame({'ts':pd.date_range(start.tz_convert('UTC')+pd.Timedelta(hours=1),end.tz_convert('UTC'),freq='h'),
                          'temp_c':20.,'irradiance_wm2':100.,'irradiance_poa_wm2':200.,'provisional':0})
    manifest={'schema_version':1,'timezone':'America/Los_Angeles','start':start.isoformat(),'end_exclusive':end.isoformat(),'files':{}}
    for name,data in [('meter',meter),('mirror',mirror),('eagle',eagle),('generation',generation),('weather',weather)]:
        path=root/f'{name}.csv'
        data[study.SPECS[name]].to_csv(path,index=False)
        manifest['files'][name]={'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':len(data)}
    (root/'manifest.json').write_text(json.dumps(manifest))
    return manifest


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, text):
        path = self.root / 'meter.csv'
        path.write_text(text)
        return path

    def test_missing_columns_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'columns'):
            study.read_source(self.write('ts,leg1_v\n2026-01-01T00:00:00Z,120\n'), 'meter')

    def test_nulls_remain_missing_and_zero_current_is_real(self):
        data = study.read_source(self.write('ts,line_v,leg1_v,leg2_v,leg1_a,leg2_a,freq\n'
            '2026-01-01T00:00:00Z,240,,120,0,0,60\n'), 'meter')
        self.assertTrue(pd.isna(data.leg1_v.iloc[0]))
        self.assertEqual(data.leg1_a.iloc[0], 0)

    def test_invalid_numeric_and_nonfinite_values_fail(self):
        for value in ['nan', 'inf', '-inf', 'bad', '-1', '400']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                study.read_source(self.write('ts,line_v,leg1_v,leg2_v,leg1_a,leg2_a,freq\n'
                    f'2026-01-01T00:00:00Z,240,{value},120,0,0,60\n'), 'meter')

    def test_duplicate_unsorted_naive_and_offgrid_times_fail(self):
        for times in [ ['2026-01-01T00:00:00Z']*2,
                       ['2026-01-01T00:05:00Z','2026-01-01T00:00:00Z'],
                       ['2026-01-01T00:00:00'], ['2026-01-01T00:00:01Z'] ]:
            with self.subTest(times=times), self.assertRaises(ValueError):
                study.read_source(self.write('ts,line_v,leg1_v,leg2_v,leg1_a,leg2_a,freq\n' +
                    ''.join(f'{t},240,120,120,0,0,60\n' for t in times)), 'meter')

    def test_snapshot_hash_schema_bounds_and_missing_file_fail_closed(self):
        with self.assertRaises(ValueError):
            study.load_snapshot(self.root)
        (self.root/'manifest.json').write_text(json.dumps({'schema_version':99}))
        with self.assertRaises(ValueError):
            study.load_snapshot(self.root)


class TimestampUnitTests(unittest.TestCase):
    def test_source_grid_validation_accepts_equivalent_parser_resolutions(self):
        # pandas parsing is pure. Force only its storage unit to exercise the
        # pandas 3 inference change on older pandas; parse every instant for real.
        parse=pd.to_datetime
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            snapshot(root)
            for unit in ['ns','us','ms','s']:
                def parse_at_unit(*args,**kwargs):
                    return parse(*args,**kwargs).as_unit(unit)
                for kind in ['meter','mirror','eagle','generation','weather']:
                    with self.subTest(unit=unit,source=kind):
                        with patch.object(study.pd,'to_datetime',side_effect=parse_at_unit):
                            got=study.read_source(root/f'{kind}.csv',kind)
                        self.assertEqual(len(got),{'meter':288,'mirror':288,'eagle':1440,'generation':288,'weather':24}[kind])
                        if kind=='generation':
                            self.assertTrue((got.src_ts==got.ts).all())

    def test_coverage_and_gaps_are_seconds_for_every_datetime_unit(self):
        times=pd.to_datetime(['2026-01-01T08:00Z','2026-01-01T08:05Z','2026-01-01T08:15Z'],utc=True)
        make_range=pd.date_range
        for unit in ['ns','us','ms','s']:
            def range_at_unit(*args,**kwargs):
                return make_range(*args,**kwargs).as_unit(unit)
            with self.subTest(unit=unit):
                data=pd.DataFrame({'ts':times.as_unit(unit),'v':[120.,120.,120.]})
                # Exercise the expected grid's storage unit as well as the input.
                with patch.object(study.pd,'date_range',side_effect=range_at_unit):
                    got=study.coverage(data,pd.Timestamp('2026-01-01T08:00Z'),pd.Timestamp('2026-01-01T08:20Z'),300,['v'])
                self.assertEqual(got['possible_slots'],4)
                self.assertEqual(got['occupied_slots'],3)
                self.assertEqual(got['represented_seconds_estimate'],900)
                self.assertEqual(got['missing_seconds'],300)
                self.assertEqual(got['max_gap_seconds'],600)
                self.assertEqual(got['interval_seconds']['p50'],450)
                self.assertEqual(got['gaps_above_1_5_cadence'],1)

    def test_source_grid_still_rejects_real_offsets_at_every_precision(self):
        cases=['2026-01-01T00:00:01Z','2026-01-01T00:00:00.001Z',
               '2026-01-01T00:00:00.000001Z','2026-01-01T00:00:00.000000001Z']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for timestamp in cases:
                with self.subTest(timestamp=timestamp):
                    meter=root/'meter.csv'
                    meter.write_text('ts,line_v,leg1_v,leg2_v,leg1_a,leg2_a,freq\n'+f'{timestamp},240,120,120,0,0,60\n')
                    with self.assertRaises(ValueError):study.read_source(meter,'meter')
                    generation=root/'generation.csv'
                    generation.write_text('ts,src_ts,gen_w,con_w\n'+f'2026-01-01T00:05:00Z,{timestamp},1000,1500\n')
                    with self.assertRaises(ValueError):study.read_source(generation,'generation')

    def test_future_source_timestamp_and_nanosecond_bounds_still_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'generation.csv'
            path.write_text('ts,src_ts,gen_w,con_w\n2026-01-01T00:00:00Z,2026-01-01T00:05:00Z,1000,1500\n')
            with self.assertRaises(ValueError):study.read_source(path,'generation')
        data=frame(['2026-01-01T08:00Z'],v=[120.])
        for start,end in [('2026-01-01T08:00:00.000000001Z','2026-01-01T08:05Z'),
                          ('2026-01-01T08:00Z','2026-01-01T08:05:00.000000001Z')]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                study.coverage(data,pd.Timestamp(start),pd.Timestamp(end),300,['v'])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.manifest=snapshot(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def change_manifest(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))

    def test_fixture_generator_reports_full_day_and_unknown_loss(self):
        result=study.build_study(self.root)
        self.assertEqual(result['coverage']['combined_voltage']['possible_slots'],288)
        self.assertEqual(result['coverage']['combined_voltage']['occupied_slots'],288)
        self.assertEqual(result['coverage']['weather_poa']['occupied_slots'],24)
        self.assertEqual(result['alignment']['matched_hourly_rows'],24)
        self.assertEqual(result['disposition']['status'],'insufficient_window')
        self.assertIsNone(result['causal_loss_kwh'])

    def test_disagreeing_poller_generation_is_not_production_truth(self):
        path=self.root/'generation.csv'
        data=pd.read_csv(path)
        data['gen_w']=500.
        data.to_csv(path,index=False)
        self.manifest['files']['generation']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        self.change_manifest()
        result=study.build_study(self.root)
        quality=result['alignment']['generation_source_reconciliation']
        self.assertEqual(quality['status'],'unresolved_disagreement')
        self.assertEqual(quality['different_source_slot_rows'],288)
        self.assertEqual(quality['series_used_for_production'],'historical_main_mirror')
        self.assertEqual(result['characterization']['gen_w'][0]['samples'],288)

    def test_extra_manifest_source_with_nonhash_private_sentinel_preserves_output(self):
        self.manifest['files']['synthetic_private_identifier']={'sha256':'synthetic private provenance text'}
        self.change_manifest()
        output=self.root/'published.json'
        output.write_text('existing output')
        with self.assertRaises(SystemExit):
            study.main(['--input-dir',str(self.root),'--output',str(output)])
        self.assertEqual(output.read_text(),'existing output')

    def test_manifest_files_needs_a_dict_with_every_declared_source(self):
        sources=self.manifest['files']
        for invalid in [None,[],list(sources),{key:value for key,value in sources.items() if key!='meter'}]:
            with self.subTest(kind=type(invalid).__name__):
                self.manifest['files']=invalid
                self.change_manifest()
                output=self.root/'published.json'
                output.write_text('existing output')
                with self.assertRaises(SystemExit):
                    study.main(['--input-dir',str(self.root),'--output',str(output)])
                self.assertEqual(output.read_text(),'existing output')

    def test_known_source_nonhash_or_wrong_hash_preserves_output(self):
        for bad_hash in ['synthetic private provenance text','0'*64]:
            with self.subTest(hash_kind='nonhash' if len(bad_hash)!=64 else 'wrong_hash'):
                self.manifest['files']['meter']['sha256']=bad_hash
                self.change_manifest()
                output=self.root/'published.json'
                output.write_text('existing output')
                with self.assertRaises(SystemExit):
                    study.main(['--input-dir',str(self.root),'--output',str(output)])
                self.assertEqual(output.read_text(),'existing output')

    def test_private_provenance_is_allowed_but_only_checked_hashes_are_public(self):
        sentinel='synthetic private provenance text'
        self.manifest['private_provenance']={'synthetic_private_note':sentinel}
        self.manifest['files']['meter']['private_note']=sentinel
        self.change_manifest()
        output=self.root/'published.json'
        study.main(['--input-dir',str(self.root),'--output',str(output)])
        text=output.read_text()
        hashes=json.loads(text)['study']['input_sha256']
        self.assertEqual(set(hashes),{'meter','mirror','eagle','generation','weather'})
        for name in ['meter','mirror','eagle','generation','weather']:
            self.assertEqual(hashes[name],hashlib.sha256((self.root/f'{name}.csv').read_bytes()).hexdigest())
        self.assertNotIn(sentinel,text)
        self.assertNotIn('synthetic_private_note',text)
        self.assertNotIn('private_note',text)

    def test_hash_mismatch_fails_before_output_change(self):
        output=self.root/'published.json'
        output.write_text('original')
        (self.root/'meter.csv').write_text('corruption')
        with self.assertRaises(SystemExit):
            study.main(['--input-dir',str(self.root),'--output',str(output)])
        self.assertEqual(output.read_text(),'original')

    def test_row_count_timezone_missing_file_and_path_escape_fail(self):
        cases=[('rows',1),('filename','../meter.csv')]
        for key,value in cases:
            with self.subTest(key=key):
                self.manifest['files']['meter'][key]=value
                self.change_manifest()
                with self.assertRaises(ValueError):study.load_snapshot(self.root)
                self.manifest=snapshot(self.root)
        self.manifest['timezone']='UTC';self.change_manifest()
        with self.assertRaises(ValueError):study.load_snapshot(self.root)
        self.manifest=snapshot(self.root)
        (self.root/'meter.csv').unlink()
        with self.assertRaises(ValueError):study.load_snapshot(self.root)

    def test_invalid_bounds_and_outside_rows_fail(self):
        for start,end in [('2026-01-02T00:00:00-08:00','2026-01-01T00:00:00-08:00'),
                          ('2026-01-01T00:05:00-08:00','2026-01-02T00:00:00-08:00')]:
            self.manifest['start']=start;self.manifest['end_exclusive']=end;self.change_manifest()
            with self.assertRaises(ValueError):study.load_snapshot(self.root)

    def test_empty_usable_voltage_fails(self):
        for name in ['meter','mirror']:
            p=self.root/f'{name}.csv'
            x=pd.read_csv(p)
            x[['line_v','leg1_v','leg2_v','freq']]=None
            x.to_csv(p,index=False)
            self.manifest['files'][name]['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        self.change_manifest()
        with self.assertRaisesRegex(ValueError,'no usable voltage'):study.build_study(self.root)

    def test_cli_deterministic_output_is_valid_json(self):
        output=self.root/'public'/'voltage.json'
        study.main(['--input-dir',str(self.root),'--output',str(output)])
        first=output.read_bytes()
        study.main(['--input-dir',str(self.root),'--output',str(output)])
        self.assertEqual(first,output.read_bytes())
        self.assertEqual(json.loads(first)['source_selection'],{'local_gateway':288})



class CoverageTests(unittest.TestCase):
    def test_dst_elapsed_duration_and_missing_are_separate(self):
        start = pd.Timestamp('2026-11-01T00:00:00-07:00')
        end = pd.Timestamp('2026-11-02T00:00:00-08:00')
        times = pd.date_range(start.tz_convert('UTC'), end.tz_convert('UTC'),freq='5min',inclusive='left')
        data = frame(times.delete(2), leg1_v=np.repeat(120.,299))
        got = study.coverage(data,start,end,300,['leg1_v'])
        self.assertEqual(got['possible_slots'],300)
        self.assertEqual(got['occupied_slots'],299)
        self.assertEqual(got['missing_seconds'],300)
        self.assertEqual(got['daily'][0]['possible_slots'],300)

    def test_long_gap_duration_is_capped_at_cadence(self):
        data = frame(['2026-01-01T08:00Z','2026-01-01T09:00Z'],leg1_v=[127.,120.])
        got = study.coverage(data,pd.Timestamp('2026-01-01T08:00Z'),pd.Timestamp('2026-01-01T10:00Z'),300,['leg1_v'])
        self.assertEqual(got['represented_seconds_estimate'],600)
        self.assertEqual(got['missing_seconds'],6600)
        self.assertEqual(got['max_gap_seconds'],3600)
        stats = study.voltage_summary(data)
        self.assertEqual(stats['leg1_v']['range_a_outside_samples'],1)
        self.assertEqual(stats['leg1_v']['outside_represented_seconds_estimate'],300)

    def test_limits_are_inclusive_and_legs_are_separate(self):
        times=pd.date_range('2026-01-01',periods=4,freq='5min',tz='UTC')
        data=frame(times,leg1_v=[114.,126.,113.9,126.1],leg2_v=[120.]*4,line_v=[234.,246.,233.9,246.1],freq=[60.]*4)
        got=study.voltage_summary(data)
        self.assertEqual(got['leg1_v']['range_a_outside_samples'],2)
        self.assertEqual(got['leg2_v']['range_a_outside_samples'],0)
        self.assertEqual(got['line_v']['range_a_outside_samples'],0)
        self.assertAlmostEqual(got['leg_difference_absolute_v']['max'],6.1)
        self.assertAlmostEqual(got['line_minus_leg_sum_v']['max'],0)

    def test_frequency_sanity_distinguishes_constant_from_variable(self):
        data=frame(pd.date_range('2026-01-01',periods=3,freq='5min',tz='UTC'),freq=[60.]*3)
        self.assertTrue(study.frequency_summary(data)['constant_at_reported_resolution'])
        data['freq']=[59.99,60.,60.01]
        self.assertFalse(study.frequency_summary(data)['constant_at_reported_resolution'])

    def test_two_samples_far_apart_are_not_sixty_days_of_observations(self):
        data=frame(['2026-01-01T00:00Z','2026-04-01T00:00Z'],leg1_v=[120.,120.],leg2_v=[120.,120.])
        self.assertEqual(study.disposition(data)['status'],'insufficient_window')

    def test_record_shorter_than_sixty_days_cannot_support_disposition(self):
        data=frame(pd.date_range('2026-01-01',periods=3,freq='5min',tz='UTC'),leg1_v=[120.]*3,leg2_v=[120.]*3)
        got=study.disposition(data)
        self.assertEqual(got['status'],'insufficient_window')
        self.assertFalse(got['service_compliance_proved'])


class JoinTests(unittest.TestCase):
    def test_deployment_day_weather_values_cannot_become_voltage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mirror.csv'
            path.write_text('ts,gen_w,con_w,line_v,leg1_v,leg2_v,freq,voltage_semantics_valid\n'
                            '2026-07-25T15:00:00Z,1000,500,240,60,1010,60,0\n'
                            '2026-07-26T15:00:00Z,1000,500,240,120,120,60,1\n')
            data=study.read_source(path,'mirror')
            self.assertTrue(pd.isna(data.leg2_v.iloc[0]))
            self.assertEqual(data.gen_w.iloc[0],1000)
            self.assertEqual(data.leg2_v.iloc[1],120)

    def test_missing_frequency_does_not_discard_local_voltage_excursion(self):
        local=frame(['2026-01-01T00:00Z'],line_v=[247.],leg1_v=[127.],leg2_v=[120.],freq=[np.nan])
        mirror=frame(['2026-01-01T00:00Z'],line_v=[240.],leg1_v=[120.],leg2_v=[120.],freq=[60.])
        combined,_=study.combine_voltage(local,mirror)
        self.assertEqual(study.voltage_summary(combined)['leg1_v']['range_a_outside_samples'],1)

    def test_local_preference_and_same_chain_overlap_check(self):
        local=frame(['2026-01-01T00:00Z'],line_v=[240.],leg1_v=[120.],leg2_v=[120.],freq=[60.])
        mirror=frame(['2026-01-01T00:00Z','2026-01-01T00:05Z'],line_v=[241.,242.],leg1_v=[120.,121.],leg2_v=[121.,121.],freq=[60.,60.])
        combined,overlap=study.combine_voltage(local,mirror)
        self.assertEqual(combined.line_v.tolist(),[240.,242.])
        self.assertEqual(combined.source.tolist(),['local_gateway','same_chain_mirror'])
        self.assertEqual(overlap['line_v']['max_absolute_difference'],1.)

    def test_eagle_alignment_has_explicit_tolerance_and_no_gap_fill(self):
        voltage=frame(['2026-01-01T00:00Z','2026-01-01T00:05Z','2026-01-01T00:10Z'],leg1_v=[120.]*3,leg2_v=[120.]*3)
        mirror=frame(voltage.ts,gen_w=[1000.,1000.,1000.],con_w=[900.,900.,900.])
        eagle=frame(['2026-01-01T00:00:20Z','2026-01-01T00:05:31Z'],demand_w=[-500.,500.])
        got=study.join_context(voltage,mirror,eagle,tolerance_seconds=30)
        self.assertEqual(got.net_grid_w.notna().sum(),1)
        self.assertEqual(got.gross_load_w.iloc[0],500.)
        self.assertTrue(pd.isna(got.gross_load_w.iloc[1]))

    def test_weather_uses_preceding_hour_not_nearest_hour(self):
        times=pd.date_range('2026-01-01T08:05Z',periods=12,freq='5min')
        joined=frame(times,leg1_v=[120.]*12,leg2_v=[120.]*12,gen_w=[1000.]*12,gross_load_w=[500.]*12,net_grid_w=[-500.]*12)
        weather=frame(['2026-01-01T09:00Z'],irradiance_poa_wm2=[200.],irradiance_wm2=[100.],temp_c=[20.],provisional=[0.])
        got=study.hourly_context(joined,weather)
        self.assertEqual(len(got),1)
        self.assertEqual(got.irradiance_poa_wm2.iloc[0],200.)
        self.assertEqual(got.valid_slots.iloc[0],12)

    def test_production_model_refuses_missing_reference(self):
        result=study.production_association(pd.DataFrame())
        self.assertEqual(result['status'],'insufficient_evidence')
        self.assertIsNone(result['causal_loss_kwh'])
        self.assertIsNone(result['causal_cost_usd'])


class ReviewBoundaryTests(unittest.TestCase):
    def complete_voltage(self, days=61):
        times=pd.date_range('2026-01-01T08:00Z',periods=days*288,freq='5min')
        return frame(times,line_v=240.,leg1_v=120.,leg2_v=120.,freq=60.)

    def test_partial_local_excursion_survives_without_mirror(self):
        local=self.complete_voltage()
        local.loc[10,['leg1_v','leg2_v']]=[127.,np.nan]
        combined,_=study.combine_voltage(local,local.iloc[:0])
        self.assertEqual(study.voltage_summary(combined)['leg1_v']['range_a_outside_samples'],1)
        self.assertEqual(study.disposition(combined)['status'],'investigate_observed_outside_range')

    def test_available_local_excursion_wins_over_normal_mirror(self):
        mirror=self.complete_voltage()
        local=mirror.copy()
        local.loc[10,['leg1_v','leg2_v']]=[127.,np.nan]
        combined,overlap=study.combine_voltage(local,mirror)
        self.assertEqual(combined.leg1_v.iloc[10],127.)
        self.assertEqual(combined.leg2_v.iloc[10],120.)
        self.assertEqual(combined.source.iloc[10],'local_gateway_with_mirror_fields')
        self.assertEqual(combined.leg1_v_source.iloc[10],'local_gateway')
        self.assertEqual(combined.leg2_v_source.iloc[10],'same_chain_mirror')
        self.assertEqual(study.disposition(combined)['status'],'investigate_observed_outside_range')
        study.clean_json(overlap)

    def test_line_only_evidence_is_not_discarded(self):
        local=self.complete_voltage()
        local.loc[10,['line_v','leg1_v','leg2_v']]=[253.,np.nan,np.nan]
        combined,_=study.combine_voltage(local,local.iloc[:0])
        got=study.disposition(combined)
        self.assertEqual(study.voltage_summary(combined)['line_v']['range_a_outside_samples'],1)
        self.assertEqual(got['status'],'investigate_observed_outside_range')
        self.assertEqual(got['line_outside_samples'],1)
        self.assertFalse(got['service_compliance_proved'])

    def test_line_excursion_with_normal_legs_needs_investigation(self):
        data=self.complete_voltage()
        data.loc[0,'line_v']=253.
        self.assertEqual(study.disposition(data)['status'],'investigate_observed_outside_range')

    def test_short_observed_excursion_does_not_receive_no_excursion_label(self):
        data=self.complete_voltage(days=1)
        data.loc[0,['leg1_v','leg2_v']]=[127.,np.nan]
        self.assertEqual(study.disposition(data)['status'],'investigate_observed_outside_range')

    def test_snapshot_rejects_offgrid_or_partial_local_date_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for field,value in [('start','2025-12-31T23:59:59-08:00'),
                                ('start','2025-12-31T23:55:00-08:00'),
                                ('end_exclusive','2026-01-02T00:05:00-08:00')]:
                with self.subTest(field=field,value=value):
                    meta=snapshot(root)
                    meta[field]=value
                    (root/'manifest.json').write_text(json.dumps(meta))
                    with self.assertRaises(ValueError):study.load_snapshot(root)

    def test_coverage_rejects_bounds_that_shift_the_source_grid(self):
        data=frame(['2026-01-01T08:00Z'],v=[1.])
        for start,end in [('2026-01-01T07:59:59Z','2026-01-01T09:00Z'),
                          ('2026-01-01T08:00Z','2026-01-01T08:02Z')]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                study.coverage(data,pd.Timestamp(start),pd.Timestamp(end),300,['v'])

    def test_explicit_end_caps_final_excursion_duration(self):
        data=frame(['2026-01-01T08:00Z'],leg1_v=[127.])
        got=study.voltage_summary(data,end=pd.Timestamp('2026-01-01T08:02Z'))
        self.assertEqual(got['leg1_v']['outside_represented_seconds_estimate'],120.)

    def test_missing_end_does_not_invent_final_sample_duration(self):
        data=frame(['2026-01-01T08:00Z'],leg1_v=[127.])
        self.assertEqual(study.voltage_summary(data)['leg1_v']['outside_represented_seconds_estimate'],0.)

    def test_latest_sixty_dates_use_calendar_denominators_through_both_dst_changes(self):
        cases=[('2026-11-14T00:00:00-08:00','2026-09-16T00:00:00-07:00','2026-11-15T00:00:00-08:00',17292),
               ('2026-04-14T00:00:00-07:00','2026-02-14T00:00:00-08:00','2026-04-15T00:00:00-07:00',17268)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for target,start,end,slots in cases:
                with self.subTest(end=end):
                    meta=snapshot(root)
                    shift=pd.Timestamp(target)-pd.Timestamp('2026-01-01T00:00:00-08:00')
                    for kind in study.SPECS:
                        path=root/f'{kind}.csv'
                        data=pd.read_csv(path)
                        for col in ['ts']+(['src_ts'] if kind=='generation' else []):
                            data[col]=pd.to_datetime(data[col],utc=True)+shift
                        data.to_csv(path,index=False)
                        meta['files'][kind]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                    meta['start']=start;meta['end_exclusive']=end
                    (root/'manifest.json').write_text(json.dumps(meta))
                    got=study.build_study(root)['latest_60_local_dates']['combined_voltage']
                    self.assertEqual(got['possible_slots'],slots)
                    self.assertEqual(len(got['daily']),60)
                    self.assertEqual(got['daily'][0]['possible_slots'],288)
                    self.assertEqual(got['daily'][0]['date'],start[:10])

    def test_generation_agreement_needs_finite_paired_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for scenario in ['empty','missing_mirror','agree']:
                with self.subTest(scenario=scenario):
                    meta=snapshot(root)
                    if scenario=='empty':
                        kind='generation'
                        data=pd.read_csv(root/f'{kind}.csv').iloc[:0]
                    elif scenario=='missing_mirror':
                        kind='mirror'
                        data=pd.read_csv(root/f'{kind}.csv')
                        data['gen_w']=None
                    else:
                        kind='generation'
                        data=pd.read_csv(root/f'{kind}.csv')
                    path=root/f'{kind}.csv'
                    data.to_csv(path,index=False)
                    meta['files'][kind].update(rows=len(data),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                    (root/'manifest.json').write_text(json.dumps(meta))
                    got=study.build_study(root)['alignment']['generation_source_reconciliation']
                    if scenario=='agree':
                        self.assertEqual(got['status'],'consistent_at_source_slot')
                        self.assertEqual(got['finite_paired_comparison_rows'],288)
                        self.assertNotIn('disagree',got['limitation'])
                    else:
                        self.assertEqual(got['status'],'insufficient_comparison_evidence')
                        self.assertEqual(got['finite_paired_comparison_rows'],0)
                    self.assertEqual(got['different_source_slot_rows'],0)

    def test_canonical_root_and_private_verify_copies_write_only_sandbox_data(self):
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory)
            root=outer/'repo'
            for path in [root/'analysis',root/'data',root/'private'/'verify',root/'private'/'1-raw-data'/'service-telemetry']:
                path.mkdir(parents=True,exist_ok=True)
            snapshot(root/'private'/'1-raw-data'/'service-telemetry')
            for relative in ['analysis/service_voltage.py','service_voltage.py','private/verify/service_voltage.py']:
                target=root/relative
                shutil.copyfile(study.__file__,target)
            output=root/'data'/'service_voltage.json'
            expected=None
            for relative in ['analysis/service_voltage.py','service_voltage.py','private/verify/service_voltage.py']:
                with self.subTest(relative=relative):
                    proc=subprocess.run([sys.executable,'-W','error',str(root/relative)],cwd=outer,capture_output=True,text=True)
                    self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
                    self.assertTrue(output.is_file())
                    if expected is None:expected=output.read_bytes()
                    self.assertEqual(output.read_bytes(),expected)
                    output.unlink()
            self.assertFalse((outer/'data'/'service_voltage.json').exists())
            self.assertFalse((root/'private'/'data'/'service_voltage.json').exists())

    def test_archive_free_root_imports_and_accepts_explicit_synthetic_input(self):
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory)
            root=outer/'repo'
            for path in [root/'analysis',root/'data',outer/'fixture']:
                path.mkdir(parents=True,exist_ok=True)
            target=root/'analysis'/'service_voltage.py'
            shutil.copyfile(study.__file__,target)
            snapshot(outer/'fixture')
            code='import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import service_voltage as s; assert s.ROOT == Path(sys.argv[2]).resolve()'
            imported=subprocess.run([sys.executable,'-W','error','-c',code,str(root/'analysis'),str(root)],cwd=outer,capture_output=True,text=True)
            self.assertEqual(imported.returncode,0,imported.stdout+imported.stderr)
            output=root/'data'/'service_voltage.json'
            explicit=subprocess.run([sys.executable,'-W','error',str(target),'--input-dir',str(outer/'fixture')],cwd=outer,capture_output=True,text=True)
            self.assertEqual(explicit.returncode,0,explicit.stdout+explicit.stderr)
            first=output.read_bytes()
            missing=subprocess.run([sys.executable,str(target)],cwd=outer,capture_output=True,text=True)
            self.assertNotEqual(missing.returncode,0)
            self.assertEqual(output.read_bytes(),first)
            self.assertFalse((root/'private').exists())

    def test_unrecognized_script_location_does_not_use_cwd_as_output_root(self):
        with tempfile.TemporaryDirectory() as directory:
            outer=Path(directory)
            root=outer/'repo'
            for path in [root/'analysis',root/'data',root/'private'/'1-raw-data'/'service-telemetry']:
                path.mkdir(parents=True,exist_ok=True)
            snapshot(root/'private'/'1-raw-data'/'service-telemetry')
            stray=outer/'stray'/'service_voltage.py'
            stray.parent.mkdir()
            shutil.copyfile(study.__file__,stray)
            proc=subprocess.run([sys.executable,str(stray)],cwd=root,capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertFalse((root/'data'/'service_voltage.json').exists())



class ProductionTests(unittest.TestCase):
    def fixture(self):
        rng=np.random.default_rng(28)
        times=pd.DatetimeIndex([pd.Timestamp('2026-07-01T16:00Z')+pd.Timedelta(days=day,hours=hour)
                                for day in range(40) for hour in range(9)])
        n=len(times)
        poa=rng.uniform(150,950,n)
        voltage=121+rng.normal(scale=.6,size=n)
        temp=rng.normal(25,4,n)
        load=rng.uniform(300,8000,n)
        gen=1000+5*poa-350*(voltage-121)+.03*load+2*temp+rng.normal(0,20,n)
        return frame(times,gen_w=gen,leg1_v=voltage,leg2_v=voltage,gross_load_w=load,
                     irradiance_poa_wm2=poa,temp_c=temp,provisional=np.zeros(n))

    def test_known_negative_conditional_slope_is_found_without_causal_loss(self):
        got=study.production_association(self.fixture())
        model=got['conditioned_model']
        self.assertEqual(got['status'],'estimated_association')
        self.assertTrue(got['inverse_association_at_95pct'])
        self.assertAlmostEqual(model['voltage_w_per_v']['estimate'],-350,delta=10)
        self.assertLess(model['voltage_w_per_v']['ci95'][1],0)
        self.assertGreater(got['expected_power_reference']['scored_hours'],300)
        self.assertIsNone(got['causal_loss_kwh'])
        self.assertIsNone(got['causal_cost_usd'])

    def test_too_few_independent_dates_refuses_production_claim(self):
        data=self.fixture().iloc[:90]
        self.assertEqual(study.production_association(data)['status'],'insufficient_evidence')

    def test_excursion_flag_calls_for_calibrated_evidence(self):
        times=pd.date_range('2026-01-01',periods=20000,freq='5min',tz='UTC')
        data=frame(times,leg1_v=np.repeat(127.,len(times)),leg2_v=np.repeat(120.,len(times)))
        got=study.disposition(data)
        self.assertEqual(got['status'],'investigate_observed_outside_range')
        self.assertFalse(got['service_compliance_proved'])



class RegressionTests(unittest.TestCase):
    def test_cluster_interval_and_coefficient_use_conditioned_relation(self):
        rng=np.random.default_rng(7)
        n=600
        x=rng.normal(size=(n,2))
        y=2*x[:,0]-3*x[:,1]+rng.normal(scale=.1,size=n)
        got=study.cluster_regression(y,x,['a','b'],np.repeat(np.arange(30),20))
        self.assertAlmostEqual(got['coefficients']['a']['estimate'],2.,delta=.03)
        self.assertAlmostEqual(got['coefficients']['b']['estimate'],-3.,delta=.03)
        ci=got['coefficients']['a']['ci95']
        self.assertLess(ci[0],2.)
        self.assertGreater(ci[1],2.)

    def test_unidentified_or_constant_regressor_is_explicit(self):
        x=np.ones((100,1))
        self.assertEqual(study.cluster_regression(np.ones(100),x,['voltage'],np.repeat(np.arange(10),10))['status'],'unidentified')


if __name__=='__main__':
    unittest.main()
