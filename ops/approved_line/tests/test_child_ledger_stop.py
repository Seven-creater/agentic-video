import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from omni_story.library.pipeline import CodexMCP


def worker(output, parent=None):
    w = CodexMCP.__new__(CodexMCP)
    w.output = Path(output)
    config = {'browser_catalog_page': dict(parent=str(parent), page=19)} if parent else {}
    w.state = SimpleNamespace(input_lock=dict(configuration=config))
    return w


class ChildLedgerStop(unittest.TestCase):
    def test_page_worker_obeys_the_task_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / 'task'; page = task / 'browser/pages/page_019'; page.mkdir(parents=True)
            w = worker(page, task)
            self.assertFalse(w._stop_requested())
            (task / 'mcp_stop').touch()
            self.assertTrue(w._stop_requested())

    def test_ordinary_worker_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = worker(tmp)
            self.assertFalse(w._stop_requested())
            (Path(tmp) / 'mcp_stop').touch()
            self.assertTrue(w._stop_requested())


if __name__ == '__main__':
    unittest.main()
