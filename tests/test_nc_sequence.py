"""Synthetic NC diagnostics only. No G-code transmission or controller claims."""
import unittest
from makera_mcp.nc import audit_nc_sequence


class NcSequenceTests(unittest.TestCase):
    def test_m5_in_header_or_comment_is_not_terminal_stop(self):
        for code in ('M5\nM3 S12000\nG1 X10 F100\nM30',
                     'M3\nG1 X10 F100\n(M5)\nM30',
                     'M3\nG1 X10 F100\n;M5\nM30'):
            result = audit_nc_sequence(code)
            self.assertFalse(result['restricted_sequence_check_passed'])
            self.assertFalse(result['explicit_spindle_stop_after_feed_motion'])

    def test_explicit_shutdown_and_modal_feed(self):
        result = audit_nc_sequence('G21 G90 G94\nM3 S12000\nM8\nG1 X10 F200\nY10\nX0\nM5\nM9\nM30')
        self.assertTrue(result['restricted_sequence_check_passed'])
        self.assertEqual(result['feed_blocks_using_inherited_f'], 2)
        self.assertEqual(result['feed_blocks_without_known_positive_f'], [])
        self.assertFalse(result['hardware_stopped_verified'])

    def test_stop_after_end_is_unreachable(self):
        result = audit_nc_sequence('M3\nG1 X5 F100\nM30\nM5')
        self.assertFalse(result['restricted_sequence_check_passed'])
        self.assertIsNone(result['last_explicit_m5_line'])

    def test_spindle_restart_and_coolant_left_on(self):
        for code in ('M3\nG1 X5 F100\nM5\nM3\nM30', 'M3 M8\nG1 X5 F100\nM5\nM30'):
            self.assertFalse(audit_nc_sequence(code)['restricted_sequence_check_passed'])

    def test_unknown_control_and_optional_stop_are_not_passed(self):
        for control in ('/M5', '#1=5', 'M98 P100', 'M100', 'M5 M3', 'G1 X1 M5'):
            code='M3\nG1 X5 F100\n'+control+'\nM30'
            self.assertFalse(audit_nc_sequence(code)['restricted_sequence_check_passed'])

    def test_unknown_controls_cannot_be_masked_by_later_valid_stop(self):
        for control in ('M100', '#1=5', 'O100 SUB', 'M5garbage', 'M5 $1'):
            result=audit_nc_sequence('M3\nG1 X5 F100\n'+control+'\nM5\nM30')
            self.assertFalse(result['restricted_sequence_check_passed'])
            self.assertTrue(result['unresolved_control_lines'])

    def test_no_end_and_nested_comments(self):
        self.assertFalse(audit_nc_sequence('M3\nG1 X1 F100\nM5')['restricted_sequence_check_passed'])
        result=audit_nc_sequence('M3\nG1 X1 F100\n(M5 (nested) M30)\nM5\nM30')
        self.assertTrue(result['restricted_sequence_check_passed'])
        self.assertEqual(result['last_explicit_m5_line'],4)

    def test_feed_is_not_assumed_and_inverse_time_needs_per_block_f(self):
        self.assertEqual(audit_nc_sequence('G1 X10')['feed_blocks_without_known_positive_f'],[1])
        self.assertEqual(audit_nc_sequence('G93 G1 X10 F2\nX0')['feed_blocks_without_known_positive_f'],[2])

    def test_percent_wrapped_footer_stops_scan(self):
        result=audit_nc_sequence('%\nM3\nG1 X1 F100\nM5\n%\nM3')
        self.assertTrue(result['restricted_sequence_check_passed'])
        self.assertEqual(result['program_end']['code'],'%')


if __name__ == '__main__':
    unittest.main()
