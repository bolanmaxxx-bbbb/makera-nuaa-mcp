import hashlib
from pathlib import Path
import unittest
import test_skill_job_gate as gates


class DeliveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=gates.JobGateTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.generic_job()
        self.job=self.fixture.job;self.caps=self.fixture.caps
        self.evidence={'revision':1,'simulation_verified':True,'operations':[{
            'id':'feature','calculated':True,'saved_verified':True,'parameters_match':True,'motion_path_verified':True}]}
    def codes(self):
        return self.fixture.codes(gates.validate_job(self.job,'delivery',self.caps,self.evidence))
    def test_missing_efficiency_review_blocks_delivery_but_reviewed_one_way_is_allowed(self):
        self.assertIn('efficiency_unreviewed',self.codes())
        self.evidence['operations'][0]['efficiency_review']={'reviewed':True,'direction_strategy':'one_way','basis':'fixture quality constraint, retrace accepted'}
        self.assertNotIn('efficiency_unreviewed',self.codes())
    def test_shutdown_report_must_match_current_nc(self):
        self.job['deliverables']['nc']=True
        p=Path(self.fixture.temp.name)/'fixture.nc';p.write_text('M5\nM30\n')
        self.assertIn('nc_shutdown_unverified',self.codes())
        self.evidence['nc_inspection']={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                                        'sequence_audit':{'restricted_sequence_check_passed':True}}
        self.assertNotIn('nc_shutdown_unverified',self.codes())
        # It still needs the separate actual postprocessing and compatibility evidence.
        self.assertIn('nc_unverified',self.codes())
        p.write_text('M3\nM30\n')
        self.assertIn('nc_shutdown_unverified',self.codes())
