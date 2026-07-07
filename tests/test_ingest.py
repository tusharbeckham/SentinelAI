"""Tests for the real-dataset loaders (CICIDS2017, UNSW-NB15).

These tests do not download the real corpora (gigabytes, and CICIDS is behind a
request form). Instead they synthesise tiny CSV fixtures carrying the exact
column names and quirks of the real files - CICIDS2017's leading spaces in
headers, its microsecond `Flow Duration`, its duplicate rows - and assert that
the loader output satisfies the same flow contract `features.py` consumes. That
is the property that matters: if the contract holds, the entire downstream
pipeline runs unchanged on real capture data.
"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from sentinelai.features import FEATURES, build_dataset
from sentinelai.ingest import (
    AUTH_COLUMNS,
    FLOW_COLUMNS,
    LOADER_CAVEATS,
    _is_external,
    _normalise_family,
    load_cicids2017,
    load_unsw_nb15,
)


def _cicids_fixture(path: Path, rows: int = 240) -> None:
    """Mimic a CICIDS2017 flow CSV, spaces in headers and all."""
    base = 1_496_800_000
    recs = []
    for i in range(rows):
        attack = i % 12 == 0
        recs.append(
            {
                " Source IP": f"192.168.10.{5 + (i % 4)}",
                " Destination IP": "8.8.8.8" if i % 5 == 0 else "192.168.10.50",
                " Destination Port": 80 if not attack else 22,
                " Timestamp": pd.to_datetime(base + i * 30, unit="s").strftime("%d/%m/%Y %H:%M:%S"),
                " Flow Duration": 250_000 + i * 10,  # microseconds
                " Total Fwd Packets": 4 + i % 7,
                " Total Backward Packets": 3 + i % 5,
                "Total Length of Fwd Packets": 400 + i,
                " Total Length of Bwd Packets": 900 + i,
                " SYN Flag Count": 1 if attack else 0,
                " Label": "FTP-Patator" if attack else "BENIGN",
            }
        )
    frame = pd.DataFrame(recs)
    # Exact duplicate rows are a documented defect of this corpus.
    frame = pd.concat([frame, frame.iloc[:20]], ignore_index=True)
    frame.to_csv(path, index=False)


def _unsw_fixture(path: Path, rows: int = 240) -> None:
    base = 1_421_900_000
    recs = []
    for i in range(rows):
        attack = i % 10 == 0
        recs.append(
            {
                "srcip": f"175.45.176.{i % 3}",
                "dstip": "149.171.126.18",
                "dsport": 53 if i % 4 == 0 else 445,
                "proto": "tcp",
                "state": "REQ" if attack else "FIN",
                "dur": 0.05 + (i % 9) * 0.01,
                "sbytes": 300 + i,
                "dbytes": 700 + i,
                "spkts": 3 + i % 5,
                "dpkts": 2 + i % 4,
                "stime": base + i * 30,
                "attack_cat": "Reconnaissance" if attack else "",
                "label": 1 if attack else 0,
            }
        )
    pd.DataFrame(recs).to_csv(path, index=False)


class TestLoaderHelpers(unittest.TestCase):
    def test_family_normalisation(self):
        self.assertEqual(_normalise_family("BENIGN"), "benign")
        self.assertEqual(_normalise_family("FTP-Patator"), "brute_force")
        self.assertEqual(_normalise_family("DDoS"), "dos")
        self.assertEqual(_normalise_family("Reconnaissance"), "portscan")
        self.assertEqual(_normalise_family(""), "benign")
        # Unknown families must survive under their own name, never be silently
        # relabelled benign - that would hide positives from the evaluation.
        self.assertEqual(_normalise_family("Heartbleed"), "heartbleed")

    def test_external_destination_detection(self):
        self.assertEqual(_is_external("8.8.8.8"), 1)
        self.assertEqual(_is_external("192.168.1.10"), 0)
        self.assertEqual(_is_external("10.0.0.4"), 0)
        self.assertEqual(_is_external("127.0.0.1"), 0)
        self.assertEqual(_is_external("not-an-ip"), 0)


class TestRealDatasetLoaders(unittest.TestCase):
    def _assert_contract(self, tel, dataset: str):
        self.assertEqual(list(tel.flows.columns), list(FLOW_COLUMNS))
        self.assertEqual(list(tel.auth.columns), list(AUTH_COLUMNS))
        self.assertFalse(tel.flows.isna().any().any())
        self.assertTrue((tel.flows["dur"] >= 0).all())
        self.assertTrue(tel.flows["ts"].is_monotonic_increasing)
        self.assertEqual(tel.config["source"], dataset)
        self.assertFalse(tel.config["synthetic"])
        # Caveats must ship with the data, not live in a README only.
        self.assertEqual(tel.config["caveats"], LOADER_CAVEATS[dataset])
        self.assertGreater(len(tel.incidents), 0)

        # The decisive property: the untouched feature builder accepts it.
        df = build_dataset(tel)
        for col in FEATURES:
            self.assertIn(col, df.columns)
        self.assertEqual(df[list(FEATURES)].isna().sum().sum(), 0)
        self.assertGreater(int(df["label"].sum()), 0)
        # Flow-only corpora: auth-derived features are legitimately all zero.
        self.assertEqual(float(df["auth_events"].abs().sum()), 0.0)

    def test_cicids2017_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Friday-WorkingHours.pcap_ISCX.csv"
            _cicids_fixture(path)
            tel = load_cicids2017([path])
            self._assert_contract(tel, "cicids2017")

            # Deduplication is on by default and must be reported.
            self.assertGreater(tel.config["rows_read"], tel.config["rows_after_dedup"])
            # Microsecond -> second conversion.
            self.assertAlmostEqual(float(tel.flows["dur"].iloc[0]), 0.25, places=2)
            self.assertIn("brute_force", set(tel.incidents["attack"]))

            kept = load_cicids2017([path], strict_dedup=False)
            self.assertEqual(kept.config["rows_read"], kept.config["rows_after_dedup"])
            self.assertGreater(len(kept.flows), len(tel.flows))

    def test_unsw_nb15_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "UNSW-NB15_1.csv"
            _unsw_fixture(path)
            tel = load_unsw_nb15([path])
            self._assert_contract(tel, "unsw-nb15")
            self.assertIn("portscan", set(tel.incidents["attack"]))
            # syn is a state-derived proxy on this corpus, and must be binary.
            self.assertTrue(set(tel.flows["syn"].unique()).issubset({0, 1}))
            self.assertGreater(int(tel.flows["syn"].sum()), 0)

    def test_missing_columns_fail_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.csv"
            pd.DataFrame({"a": [1], "b": [2]}).to_csv(path, index=False)
            with self.assertRaises(KeyError):
                load_cicids2017([path])

    def test_missing_file_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            load_unsw_nb15(["/nonexistent/path/to/UNSW-NB15_1.csv"])

    def test_one_window_one_label_with_specific_family_winning(self):
        """DoS labels are the most numerous; a more specific family must win."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.csv"
            ts = pd.to_datetime(1_496_800_000, unit="s").strftime("%d/%m/%Y %H:%M:%S")
            rows = []
            for label in ("DDoS", "DDoS", "Infiltration"):
                rows.append(
                    {
                        " Source IP": "10.0.0.9",
                        " Destination IP": "10.0.0.10",
                        " Destination Port": 445,
                        " Timestamp": ts,
                        " Flow Duration": 1000,
                        " Total Fwd Packets": 2,
                        " Total Backward Packets": 1,
                        "Total Length of Fwd Packets": 100,
                        " Total Length of Bwd Packets": 200,
                        " SYN Flag Count": 1,
                        " Label": label,
                    }
                )
            pd.DataFrame(rows).to_csv(path, index=False)
            tel = load_cicids2017([path], strict_dedup=False)
            self.assertEqual(len(tel.incidents), 1)
            self.assertEqual(tel.incidents["attack"].iloc[0], "lateral_movement")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
