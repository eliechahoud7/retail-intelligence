"""Tests for the dbt integration (Task 5.4, Milestone 1): wrapper, credentials, schema protection.

The wrapper tests need neither dbt nor PostgreSQL. The schema-guard tests run the real dbt CLI of
.venv-dbt (``dbt run-operation``, which executes no SQL) and are skipped if dbt or PostgreSQL is
not available.
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.database import __main__ as cli
from src.database import dbt_runner
from src.database.connection import ENV_FILE, DatabaseConfigError, Settings, read_env_file
from tests.test_warehouse import DB

SETTINGS = Settings(host="db.example", port=6543, user="retail_app", password="NOT-A-REAL-SECRET",
                    database="retail_intelligence", connect_timeout=7, admin_user="postgres",
                    admin_password="ALSO-NOT-REAL")
DBT_INSTALLED = (dbt_runner.DBT_VENV / ("Scripts/dbt.exe" if dbt_runner.os.name == "nt" else "bin/dbt")).is_file()


class DbtEnvironmentTests(unittest.TestCase):
    def test_child_gets_exactly_the_application_role_settings(self):
        base = {"PATH": "x", "POSTGRES_ADMIN_USER": "postgres", "POSTGRES_ADMIN_PASSWORD": "admin-secret",
                "POSTGRES_PASSWORD": "stale", "DBT_ENV_SECRET_OTHER": "other-secret", "POSTGRES_HOST": "stale"}
        env = dbt_runner.dbt_environment(SETTINGS, base)
        self.assertEqual(env["PATH"], "x")                                          # rest of the environment kept
        self.assertEqual((env["POSTGRES_HOST"], env["POSTGRES_PORT"], env["POSTGRES_USER"], env["POSTGRES_DB"],
                          env["POSTGRES_CONNECT_TIMEOUT"]), ("db.example", "6543", "retail_app", "retail_intelligence", "7"))
        self.assertEqual(env[dbt_runner.PASSWORD_VARIABLE], "NOT-A-REAL-SECRET")   # dbt scrubs DBT_ENV_SECRET_*
        for gone in ("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD", "POSTGRES_PASSWORD", "DBT_ENV_SECRET_OTHER"):
            self.assertNotIn(gone, env)                                             # no admin login, no stray secrets
        self.assertNotIn("ALSO-NOT-REAL", env.values())
        self.assertEqual([k for k, v in env.items() if v == "NOT-A-REAL-SECRET"], [dbt_runner.PASSWORD_VARIABLE])
        self.assertEqual((env["DBT_PROFILES_DIR"], env["DBT_PROJECT_DIR"]), (str(dbt_runner.DBT_PROJECT_DIR),) * 2)
        self.assertEqual(env["DBT_SEND_ANONYMOUS_USAGE_STATS"], "false")

    def test_missing_password_is_a_config_error(self):
        no_password = Settings(host="h", port=5432, user="retail_app", password="", database="retail_intelligence")
        with self.assertRaises(DatabaseConfigError):
            dbt_runner.dbt_environment(no_password, {})

    def test_run_dbt_passes_arguments_and_returns_the_exit_code(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 3))
        with mock.patch.object(dbt_runner, "dbt_executable", return_value=Path("dbt.exe")):
            code = dbt_runner.run_dbt(["debug", "--target", "dev"], SETTINGS, runner=runner)
        self.assertEqual(code, 3)
        (cmd,), kwargs = runner.call_args
        self.assertEqual(cmd, ["dbt.exe", "debug", "--target", "dev"])
        self.assertEqual(kwargs["cwd"], str(dbt_runner.DBT_PROJECT_DIR))
        self.assertEqual(kwargs["env"][dbt_runner.PASSWORD_VARIABLE], "NOT-A-REAL-SECRET")
        self.assertNotIn("NOT-A-REAL-SECRET", " ".join(cmd))                    # never on the command line

    def test_missing_dbt_environment_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(dbt_runner.DbtNotInstalledError) as ctx:
            dbt_runner.dbt_executable(Path(tmp) / ".venv-dbt")
        self.assertIn("requirements-dbt-lock.txt", str(ctx.exception))


class DbtCliTests(unittest.TestCase):
    def main(self, argv, **patches):
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(cli, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(err))
            return cli.main(argv), err.getvalue()

    def test_arguments_after_dbt_are_passed_through_unparsed(self):
        run = mock.Mock(return_value=0)
        code, _ = self.main(["dbt", "run-operation", "x", "--args", "{a: 1}", "--name", "n"], run_dbt=run)
        self.assertEqual(code, 0)
        run.assert_called_once_with(["run-operation", "x", "--args", "{a: 1}", "--name", "n"])

    def test_dbt_exit_code_is_returned(self):
        self.assertEqual(self.main(["dbt", "debug"], run_dbt=mock.Mock(return_value=2))[0], 2)

    def test_dbt_debug_is_forwarded_to_dbt(self):
        run, settings = mock.Mock(return_value=0), mock.Mock()
        code, _ = self.main(["dbt", "debug"], run_dbt=run, load_settings=settings)
        self.assertEqual(code, 0)
        run.assert_called_once_with(["debug"])
        settings.assert_not_called()                                    # dbt_runner loads settings, not the CLI

    def test_dbt_after_an_option_is_a_clear_error(self):
        for argv in (["--limit", "5", "dbt", "debug"], ["--admin", "dbt", "debug"], ["--admin", "dbt"],
                     ["check", "dbt"]):
            with self.subTest(argv):
                run, settings, check, err = mock.Mock(), mock.Mock(), mock.Mock(), io.StringIO()
                with mock.patch.object(cli, "run_dbt", run), mock.patch.object(cli, "load_settings", settings), \
                        mock.patch.object(cli, "check_connection", check), contextlib.redirect_stderr(err), \
                        self.assertRaises(SystemExit) as ctx:
                    cli.main(argv)
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn("'dbt' must be the first argument", err.getvalue())
                for never in (run, settings, check):                    # nothing runs, no connection check
                    never.assert_not_called()

    def test_dbt_is_not_a_regular_command_choice(self):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertNotIn("verify-queries,dbt", out.getvalue())
        self.assertIn("must be the FIRST", " ".join(out.getvalue().split()))

    def test_existing_database_commands_still_route(self):
        commands = ["check", "create-db", "setup-app-role", "show-role", "create-warehouse", "verify-warehouse",
                    "load-warehouse", "validate-load", "create-views", "verify-views", "run-query",
                    "verify-queries"]
        for command in commands:
            with self.subTest(command):
                run = mock.Mock()
                settings = mock.Mock(side_effect=DatabaseConfigError("stop before any connection"))
                code, err = self.main([command], run_dbt=run, load_settings=settings)
                self.assertEqual(code, 1)                               # reached the command, not argparse
                self.assertIn("CONFIG ERROR: stop before any connection", err)
                settings.assert_called_once_with()
                run.assert_not_called()

    def test_existing_check_command_behaviour_is_unchanged(self):
        settings = mock.Mock(**{"safe_url.return_value": "postgresql://u@h/db"})
        check = mock.Mock(return_value={"database": "db", "user": "u", "server_version": "16", "version": "PG 16"})
        out = io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=settings), \
                mock.patch.object(cli, "check_connection", check), mock.patch.object(cli, "run_dbt") as run, \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["check", "--admin"]), 0)
        check.assert_called_once_with(settings, admin=True)
        run.assert_not_called()
        self.assertIn("connected: database=db user=u server_version=16", out.getvalue())

    def test_dbt_as_an_option_value_is_not_treated_as_the_dbt_command(self):
        get = mock.Mock(side_effect=cli.UnknownQueryError("unknown query 'dbt'"))
        code, err = self.main(["run-query", "--name", "dbt"], run_dbt=mock.Mock(), get_query=get,
                              load_settings=mock.Mock())
        self.assertEqual(code, 1)
        get.assert_called_once_with("dbt")

    def test_missing_dbt_is_a_config_error(self):
        with mock.patch.object(dbt_runner, "load_settings", return_value=SETTINGS), \
                mock.patch.object(dbt_runner, "DBT_VENV", Path(tempfile.gettempdir()) / "no-such-venv-dbt"):
            code, err = self.main(["dbt", "debug"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("CONFIG ERROR: dbt is not installed"), err)


class DbtProjectFilesTests(unittest.TestCase):
    """The committed dbt files hold no credentials, and generated dbt files are git-ignored."""

    def committed_dbt_files(self):
        root = dbt_runner.DBT_PROJECT_DIR
        return [p for p in root.rglob("*") if p.is_file()
                and not {"target", "logs", "dbt_packages"} & set(p.relative_to(root).parts)]

    def test_profile_reads_every_connection_value_from_the_environment(self):
        profile = (dbt_runner.DBT_PROJECT_DIR / "profiles.yml").read_text(encoding="utf-8")
        for key, var in (("host", "POSTGRES_HOST"), ("port", "POSTGRES_PORT"), ("user", "POSTGRES_USER"),
                         ("password", dbt_runner.PASSWORD_VARIABLE), ("dbname", "POSTGRES_DB")):
            self.assertRegex(profile, rf"(?m)^\s+{key}: \"\{{\{{ env_var\('{var}'\)", key)
        self.assertEqual(len(re.findall(r"(?m)^\s+password:", profile)), 1)

    def test_no_local_secret_appears_in_dbt_or_requirement_files(self):
        secrets = [v for k, v in read_env_file(ENV_FILE).items() if "PASSWORD" in k and len(v) >= 4]
        if not secrets:
            self.skipTest("no local .env passwords to look for")
        root = dbt_runner.PROJECT_ROOT
        files = self.committed_dbt_files() + [root / "requirements-dbt.txt", root / "requirements-dbt-lock.txt",
                                              root / ".gitignore"]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for secret in secrets:
                self.assertNotIn(secret, text, f"{path}: contains a local password")

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_dbt_environment_and_generated_files_are_ignored_but_the_project_is_not(self):
        root = dbt_runner.PROJECT_ROOT

        def ignored(path):
            return subprocess.run(["git", "check-ignore", "-q", path], cwd=root).returncode == 0

        for path in (".venv-dbt/Scripts/dbt.exe", "dbt/target/manifest.json", "dbt/logs/dbt.log", "dbt/.user.yml",
                     "dbt/dbt_packages/x/dbt_project.yml", ".env"):
            self.assertTrue(ignored(path), path)
        for path in ("dbt/dbt_project.yml", "dbt/profiles.yml", "dbt/macros/generate_schema_name.sql",
                     "requirements-dbt.txt", "requirements-dbt-lock.txt"):
            self.assertFalse(ignored(path), path)


@unittest.skipUnless(DBT_INSTALLED and DB, "dbt (.venv-dbt) or PostgreSQL not available")
class SchemaGuardTests(unittest.TestCase):
    """macros/generate_schema_name.sql, executed by the real dbt CLI (run-operation runs no SQL)."""

    def resolve(self, schema):
        result = {}

        def runner(cmd, **kwargs):
            completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
            result["output"] = completed.stdout + completed.stderr
            return completed

        code = dbt_runner.run_dbt(["--no-use-colors", "run-operation", "generate_schema_name",
                                   "--args", f"{{custom_schema_name: '{schema}', node: null}}"], runner=runner)
        return code, result["output"]

    def test_protected_schemas_are_refused(self):
        for schema in ("warehouse", "analytics", " Warehouse ", "ANALYTICS"):
            with self.subTest(schema):
                code, out = self.resolve(schema)
                self.assertNotEqual(code, 0, out)
                self.assertIn("is PROTECTED", out)

    def test_only_the_three_dbt_schemas_are_allowed(self):
        code, out = self.resolve("public")
        self.assertNotEqual(code, 0, out)
        self.assertIn("is not a dbt schema", out)
        for schema in ("dbt_staging", "dbt_intermediate", "dbt_marts"):
            with self.subTest(schema):
                code, out = self.resolve(schema)
                self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
