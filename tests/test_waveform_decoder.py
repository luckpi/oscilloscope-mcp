import struct
import unittest

from oscilloscope_mcp.drivers.keysight_waveform import decode_word
from oscilloscope_mcp.ieee488 import definite_block


class WaveformDecoderTest(unittest.TestCase):
    def setUp(self):
        self.preamble = "+1,+0,+4,+1,2E-6,-5E-6,+1,0.02,1.25,+32768"
        codes = struct.pack("<4H", 32758, 32768, 0, 32818)
        self.message = b"#800000008" + codes + b"\n"

    def test_time_reference_voltage_reference_and_missing_point(self):
        record = decode_word(2, "V", self.preamble, self.message)
        self.assertEqual((record.channel, record.unit, record.sample_count), (2, "V", 4))
        for index, expected in enumerate((-7e-6, -5e-6, -3e-6, -1e-6)):
            self.assertAlmostEqual(record.time_at(index), expected, places=14)
        for index, expected in ((0, 1.05), (1, 1.25), (3, 2.25)):
            self.assertAlmostEqual(record.value_at(index), expected)
        self.assertIsNone(record.value_at(2))
        self.assertEqual(list(record.raw_codes), [32758, 32768, 0, 32818])

    def test_rejects_mismatched_count_or_unsupported_peak_layout(self):
        for preamble in (
            self.preamble.replace("+4", "+5", 1),
            self.preamble.replace("+0", "+1", 1),
            self.preamble.replace("2E-6", "0", 1),
        ):
            with self.subTest(preamble=preamble), self.assertRaises(ValueError):
                decode_word(2, "V", preamble, self.message)

    def test_definite_block_validates_length_without_stripping_payload(self):
        self.assertEqual(definite_block(b"#13a\n\n\r\n", max_bytes=3), b"a\n\n")
        self.assertEqual(definite_block(b"#13a\n\n", max_bytes=3), b"a\n\n")
        for message in (b"#0abc\n", b"#13ab", b"#13abcX", b"#1Xa", b"#14abcd"):
            with self.subTest(message=message), self.assertRaises(ValueError):
                definite_block(message, max_bytes=3)
