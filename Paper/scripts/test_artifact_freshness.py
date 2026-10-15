"""Distinguish Git checkout timestamps from changed manuscript build inputs."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from verify_artifact import check_pdf_freshness, digest


class ArtifactFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paper = Path(self.directory.name)
        (self.paper / 'build').mkdir()
        self.source = self.paper / 'manuscript.tex'
        self.source.write_text('Verified manuscript input')
        self.pdf = self.paper / 'build/manuscript.pdf'
        self.pdf.write_bytes(b'Verified PDF bytes')
        self.receipt = self.paper / 'build/verification.json'
        self.receipt.write_text(json.dumps(dict(
            pdf_sha256=digest(self.pdf),
            compiled_input_sha256={'manuscript.tex': digest(self.source)})))
        os.utime(self.pdf, ns=(1_000_000_000, 1_000_000_000))
        os.utime(self.source, ns=(2_000_000_000, 2_000_000_000))

    def test_identical_recorded_build_survives_checkout_timestamp_order(self):
        method, inputs = check_pdf_freshness(self.pdf, [self.source], self.paper)
        self.assertEqual(method, 'recorded_content_hashes')
        self.assertEqual(inputs, {'manuscript.tex': digest(self.source)})

    def test_changed_source_cannot_reuse_the_receipt(self):
        self.source.write_text('Changed manuscript input')
        self.assertRaises(ValueError, check_pdf_freshness,
                          self.pdf, [self.source], self.paper)

    def test_changed_pdf_or_input_membership_cannot_reuse_the_receipt(self):
        self.pdf.write_bytes(b'Different PDF bytes')
        os.utime(self.pdf, ns=(1_000_000_000, 1_000_000_000))
        self.assertRaises(ValueError, check_pdf_freshness,
                          self.pdf, [self.source], self.paper)
        self.pdf.write_bytes(b'Verified PDF bytes')
        extra = self.paper / 'new_table.tex'
        extra.write_text('Additional build input')
        os.utime(self.pdf, ns=(1_000_000_000, 1_000_000_000))
        self.assertRaises(ValueError, check_pdf_freshness,
                          self.pdf, [self.source, extra], self.paper)

    def test_new_local_build_uses_timestamp_gate(self):
        self.receipt.unlink()
        self.assertRaises(ValueError, check_pdf_freshness,
                          self.pdf, [self.source], self.paper)
        os.utime(self.pdf, ns=(3_000_000_000, 3_000_000_000))
        self.assertEqual(check_pdf_freshness(self.pdf, [self.source], self.paper)[0],
                         'local_build_timestamps')


if __name__ == '__main__':
    unittest.main()
