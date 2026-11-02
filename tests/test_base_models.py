"""Build the three base models and check that verify_model.py passes on each.

Run: python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (schema in verticals/, answers in answers/)
BASE_MODELS = [
    ("saas.yaml", "saas_base.yaml"),
    ("power_project.yaml", "power_base.yaml"),
    ("balance.yaml", "balance_base.yaml"),
]


def run(args):
    p = subprocess.run([sys.executable] + args, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=600)
    return p.returncode, p.stdout


class BaseModelsVerify(unittest.TestCase):

    def check_model(self, schema, answers):
        schema_path = os.path.join("verticals", schema)
        answers_path = os.path.join("answers", answers)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, answers.replace(".yaml", ".xlsx"))
            code, text = run(["engine/build_model.py", schema_path, answers_path, out])
            self.assertEqual(code, 0, "build failed:\n" + text)
            code, text = run(["engine/verify_model.py", schema_path, out])
            self.assertEqual(code, 0, "verify_model failed:\n" + text)

            self.check_start_here(out)

    def check_start_here(self, path):
        """Start_Here opens first, links every sheet it names, and its results
        equal the cells they summarise (the two checks read 0)."""
        import openpyxl
        wb = openpyxl.load_workbook(path)
        self.assertEqual(wb.sheetnames[0], "Start_Here")
        self.assertEqual(wb.active.title, "Start_Here")
        ws = wb["Start_Here"]
        targets = {c.hyperlink.location.split("!")[0].strip("'")
                   for row in ws.iter_rows() for c in row if c.hyperlink is not None}
        self.assertTrue(targets <= set(wb.sheetnames), targets - set(wb.sheetnames))
        self.assertIn("Dashboard", targets)
        rows = [r for r in range(1, ws.max_row + 1)
                if str(ws.cell(row=r, column=3).value or "").startswith("=")]
        self.assertGreaterEqual(len(rows), 7)
        try:
            import formulas
        except ImportError:
            return
        sol = formulas.ExcelModel().loads(path).finish().calculate()
        vals = {k.split("]")[-1].upper(): v.value[0][0] for k, v in sol.items()
                if "]" in k and "!" in k and ":" not in k and hasattr(v, "value")}
        for r in rows:
            label = ws.cell(row=r, column=2).value
            got = vals["START_HERE'!C%d" % r]
            if "must read 0" in label:
                self.assertLess(abs(got), 1e-6, label)
            else:
                self.assertNotIn(got, (None, ""), label)

    def test_saas(self):
        self.check_model(*BASE_MODELS[0])

    def test_power_project(self):
        self.check_model(*BASE_MODELS[1])

    def test_balance(self):
        self.check_model(*BASE_MODELS[2])


if __name__ == "__main__":
    unittest.main()
