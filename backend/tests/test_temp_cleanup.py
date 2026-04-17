import os
import tempfile
import time
import unittest

import app as app_module
from app import cleanup_expired_sessions


class TempCleanupTests(unittest.TestCase):
    def test_cleanup_expired_sessions_deletes_old_dirs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_dir = os.path.join(tmp_dir, "old-session")
            fresh_dir = os.path.join(tmp_dir, "fresh-session")
            os.makedirs(old_dir, exist_ok=True)
            os.makedirs(fresh_dir, exist_ok=True)

            now = time.time()
            old_mtime = now - (2 * 3600)
            os.utime(old_dir, (old_mtime, old_mtime))
            os.utime(fresh_dir, (now, now))

            original_temp_dir = app_module.TEMP_DIR
            original_retention = app_module.TEMP_RETENTION_HOURS
            app_module.TEMP_DIR = tmp_dir
            app_module.TEMP_RETENTION_HOURS = 1
            try:
                deleted = cleanup_expired_sessions()
            finally:
                app_module.TEMP_DIR = original_temp_dir
                app_module.TEMP_RETENTION_HOURS = original_retention

            self.assertEqual(deleted, 1)
            self.assertFalse(os.path.exists(old_dir))
            self.assertTrue(os.path.exists(fresh_dir))


if __name__ == "__main__":
    unittest.main()
