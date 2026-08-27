import unittest

from app.services.documents.multimodal_analysis import (
    extract_table_clauses,
    identify_signature_seal_regions,
    locate_evidence_pages,
    validate_ocr_confidence,
)


class MultimodalDocumentAnalysisTests(unittest.TestCase):
    def _word(self, text, left, top=10, conf=92, width=32):
        return {
            "text": text,
            "left": left,
            "top": top,
            "right": left + width,
            "width": width,
            "height": 18,
            "conf": conf,
        }

    def test_ocr_confidence_marks_low_quality_spans(self):
        words = [self._word("付款", 0, conf=95), self._word("期限", 60, conf=30)]
        result = validate_ocr_confidence(words, "付款 期限")
        self.assertEqual(result["word_count"], 2)
        self.assertEqual(result["low_confidence_ratio"], 0.5)
        self.assertTrue(result["review_required"])
        self.assertEqual(result["status"], "review")

    def test_signature_and_seal_regions_are_separate_candidates(self):
        words = [self._word("甲方签字", 0), self._word("乙方盖章", 140)]
        regions = identify_signature_seal_regions(words, ocr_confidence=0.9)
        self.assertEqual({item["type"] for item in regions}, {"seal", "signature"})
        self.assertTrue(all(item["bbox"] for item in regions))

    def test_table_rows_become_legal_clauses(self):
        words = [
            self._word("事项", 0, top=10),
            self._word("约定", 120, top=10),
            self._word("付款", 0, top=40),
            self._word("验收后30日", 120, top=40, width=70),
            self._word("其他", 0, top=70),
            self._word("普通说明", 120, top=70, width=70),
        ]
        tables, clauses = extract_table_clauses(words, page_number=3, ocr_confidence=0.8)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["page_number"], 3)
        self.assertEqual(len(clauses), 1)
        self.assertEqual(clauses[0]["clause_type"], "payment")

    def test_locate_evidence_returns_page_and_bbox(self):
        analysis = {
            "pages": [
                {
                    "page_number": 7,
                    "text": "付款期限为验收后30日。",
                    "ocr": {"confidence": 0.81},
                    "layout": {"blocks": [{"text": "付款期限为验收后30日。", "bbox": {"left": 1, "top": 2}}]},
                }
            ]
        }
        matches = locate_evidence_pages(analysis, "付款期限")
        self.assertEqual(matches[0]["page_number"], 7)
        self.assertTrue(matches[0]["exact"])
        self.assertEqual(matches[0]["bbox"]["left"], 1)

    def test_locate_evidence_can_use_visual_region_without_ocr_text(self):
        analysis = {
            "pages": [
                {
                    "page_number": 9,
                    "text": "",
                    "ocr": {"confidence": 0.2},
                    "layout": {"blocks": []},
                    "vision": {"regions": [{"label": "公章", "evidence": "右下角红色公章", "bbox": {"left": 80}}]},
                }
            ]
        }
        matches = locate_evidence_pages(analysis, "公章")
        self.assertEqual(matches[0]["page_number"], 9)
        self.assertEqual(matches[0]["source"], "multimodal_page")
        self.assertEqual(matches[0]["bbox"]["left"], 80)
