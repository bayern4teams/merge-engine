"""Load a public ER benchmark into origin databases on the disposable bench server.

Usage: python -m scripts.verify_local.load_bench <scenario> [--force]

Writes ONLY to 127.0.0.1:5439 (container merge_bench_pg, user decision 2026-10-12).
One origin database per source, named ``bm_<scenario>_<source>``; every origin holds
one table named after the entity, primary key ``id`` = the dataset's own record id.
The truth (record -> true cluster, from the published mapping) goes to
``.tmp/benchmarks/truth/<scenario>.json`` as ``[{origin, table, id, cluster}]``.
Truth ids never leave their own scenario.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras

HOST, PORT, USER = "127.0.0.1", 5439, "postgres"
ROOT = Path(__file__).resolve().parents[2]
BM = ROOT / ".tmp" / "benchmarks"


def _csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(text.splitlines(keepends=True), skipinitialspace=True))
    return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rows]


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.p[self.find(a)] = self.find(b)


def _pairs_truth(sources: dict[str, list[dict]], pairs: list[tuple[str, str, str, str]]):
    """Clusters = connected components of the published pair mapping."""
    uf = _UF()
    for s, rows in sources.items():
        for r in rows:
            uf.find(f"{s}\0{r['id']}")
    for sa, a, sb, b in pairs:
        uf.union(f"{sa}\0{a}", f"{sb}\0{b}")
    return {k: uf.find(k) for k in list(uf.p)}


def _two_source(folder: str, fa: str, fb: str, mapping: str, sa: str, sb: str):
    d = BM / folder
    src = {sa: _csv(d / fa), sb: _csv(d / fb)}
    m = _csv(d / mapping)
    ka, kb = list(m[0].keys())
    return src, _pairs_truth(src, [(sa, r[ka], sb, r[kb]) for r in m])


def _febrl(files: dict[str, str], split: int = 0):
    src: dict[str, list[dict]] = {}
    for s, f in files.items():
        src[s] = [dict(r, id=r.pop("rec_id")) for r in _csv(BM / "febrl" / f)]
    if split:  # one file dealt into `split` origins by a seeded shuffle
        (only,) = src.values()
        rng = random.Random(0)
        rows = sorted(only, key=lambda r: r["id"])
        rng.shuffle(rows)
        src = {f"part{i + 1}": rows[i::split] for i in range(split)}
    truth = {f"{s}\0{r['id']}": r["id"].split("-")[1] for s, rows in src.items() for r in rows}
    return src, truth


def _plant_febrl4(n: int = 20):
    """Febrl4 + ``n`` bridge records in origin fb (user decision 2026-10-12, pass rule 8).

    Bridge i = name + date of birth of person A_i, address + soc_sec_id of a different
    person B_i (both taken from fa originals); its truth is its own singleton, so any
    cluster joining A_i and B_i through it is a planted over-merge.
    """
    src, truth = _febrl({"fa": "dataset4a.csv", "fb": "dataset4b.csv"})
    rng = random.Random(0)
    people = sorted(src["fa"], key=lambda r: r["id"])
    picks = rng.sample(people, 2 * n)
    name = ("given_name", "surname", "date_of_birth")
    for i in range(n):
        a, b = picks[2 * i], picks[2 * i + 1]
        bridge = {k: (a[k] if k in name else b[k]) for k in a if k != "id"}
        bridge["id"] = f"bridge-{i}-{a['id']}-{b['id']}"
        src["fb"].append(bridge)
        truth[f"fb\0{bridge['id']}"] = f"bridge-{i}"
    return src, truth


def _affiliations():
    d = BM / "affiliations"
    rows = [{"id": r["id1"], "name": r["affil1"]} for r in _csv(d / "affiliationstrings_ids.csv")]
    pairs = [tuple(x) for x in csv.reader((d / "affiliationstrings_mapping.csv").open()) if x]
    src = {"affil": rows}
    return src, _pairs_truth(src, [("affil", a.strip(), "affil", b.strip()) for a, b in pairs])


def _geo():
    d = BM / "geo_settlements" / "geographicalSettelments"
    recs = [json.loads(x) for x in (d / "settlements.json").open(encoding="utf-8") if x.strip()]
    cl = [json.loads(x) for x in next(d.glob("combined*")).open(encoding="utf-8") if x.strip()]
    owner: dict[str, list[str]] = defaultdict(list)
    for c in cl:
        for v in c["data"]["clusteredVertices"]:
            owner[str(v)].append(str(c["id"]))
    ambiguous = {k for k, v in owner.items() if len(v) > 1}  # GT defect: id in 2 clusters
    src: dict[str, list[dict]] = defaultdict(list)
    truth = {}
    for r in recs:
        rid = str(r["id"])
        if rid in ambiguous:
            continue
        data = r["data"]
        s = data["ontology"].split("//")[-1].split(".")[-2]  # e.g. dbpedia
        src[s].append(
            {
                "id": rid,
                "label": data.get("label", ""),
                "lat": data.get("lat", ""),
                "lon": data.get("lon", ""),
                "type": data.get("type", ""),
                "elevation": data.get("ele", ""),
            }
        )
        truth[f"{s}\0{rid}"] = owner[rid][0]
    print(f"geo: excluded ambiguous ground-truth ids {sorted(ambiguous)}")
    return dict(src), truth


def _musicbrainz():
    rows = _csv(BM / "musicbrainz20k" / "musicbrainz-20-A01.csv.dapo")
    src: dict[str, list[dict]] = defaultdict(list)
    truth = {}
    for r in rows:
        s = f"src{r['SourceID']}"
        src[s].append(
            {
                "id": r["TID"],
                **{
                    k: r[k]
                    for k in ("number", "title", "length", "artist", "album", "year", "language")
                },
            }
        )
        truth[f"{s}\0{r['TID']}"] = r["CID"]
    return dict(src), truth


SCENARIOS = {
    # name: (entity table, builder)
    "febrl3": ("person", lambda: _febrl({"febrl": "dataset3.csv"})),
    "febrl3x2": ("person", lambda: _febrl({"febrl": "dataset3.csv"}, split=2)),
    "febrl4": ("person", lambda: _febrl({"fa": "dataset4a.csv", "fb": "dataset4b.csv"})),
    "affiliations": ("organisation", _affiliations),
    "plant_febrl4": ("person", _plant_febrl4),
    "abt_buy": (
        "product",
        lambda: _two_source(
            "abt_buy", "Abt.csv", "Buy.csv", "abt_buy_perfectMapping.csv", "abt", "buy"
        ),
    ),
    "amazon_google": (
        "product",
        lambda: _two_source(
            "amazon_google",
            "Amazon.csv",
            "GoogleProducts.csv",
            "Amzon_GoogleProducts_perfectMapping.csv",
            "amazon",
            "google",
        ),
    ),
    "dblp_acm": (
        "publication",
        lambda: _two_source(
            "dblp_acm", "DBLP2.csv", "ACM.csv", "DBLP-ACM_perfectMapping.csv", "dblp", "acm"
        ),
    ),
    "dblp_scholar": (
        "publication",
        lambda: _two_source(
            "dblp_scholar",
            "DBLP1.csv",
            "Scholar.csv",
            "DBLP-Scholar_perfectMapping.csv",
            "dblp",
            "scholar",
        ),
    ),
    "geo": ("settlement", _geo),
    "musicbrainz": ("recording", _musicbrainz),
}


def connect(db: str = "postgres"):
    assert (HOST, PORT) == ("127.0.0.1", 5439), "bench writes only go to merge_bench_pg"
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, dbname=db)
    conn.autocommit = True
    return conn


def recreate_db(name: str) -> None:
    assert name.startswith("bm_"), name
    c = connect()  # no `with`: psycopg2's context manager opens a transaction
    try:
        with c.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        c.close()


def load(scenario: str) -> dict:
    table, build = SCENARIOS[scenario]
    src, truth = build()
    out, origins = [], []
    for s, rows in sorted(src.items()):
        db = f"bm_{scenario}_{s}".lower()
        recreate_db(db)
        cols = sorted({k for r in rows for k in r} - {"id"})
        with connect(db) as c, c.cursor() as cur:
            ddl = ", ".join(f'"{k}" TEXT' for k in cols)
            cur.execute(f'CREATE TABLE "{table}" ("id" TEXT PRIMARY KEY, {ddl})')
            psycopg2.extras.execute_values(
                cur,
                f'INSERT INTO "{table}" ("id", {", ".join(chr(34) + k + chr(34) for k in cols)}) VALUES %s',
                [[r["id"]] + [r.get(k) or None for k in cols] for r in rows],
            )
            cur.execute(f'SELECT count(*) FROM "{table}"')
            assert cur.fetchone()[0] == len(rows)
        origins.append({"source": s, "database": db, "rows": len(rows), "columns": cols})
        out += [
            {
                "origin": s,
                "database": db,
                "table": table,
                "id": r["id"],
                "cluster": truth[f"{s}\0{r['id']}"],
            }
            for r in rows
        ]
    path = BM / "truth" / f"{scenario}.json"
    path.parent.mkdir(exist_ok=True)
    meta = {"scenario": scenario, "table": table, "origins": origins, "records": out}
    path.write_text(json.dumps(meta))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=[*sorted(SCENARIOS), "all"])
    a = ap.parse_args()
    for sc in sorted(SCENARIOS) if a.scenario == "all" else [a.scenario]:
        m = load(sc)
        clusters = defaultdict(set)
        for r in m["records"]:
            clusters[r["cluster"]].add((r["origin"], r["id"]))
        multi = sum(1 for v in clusters.values() if len(v) > 1)
        print(
            sc,
            [(o["database"], o["rows"]) for o in m["origins"]],
            f"records={len(m['records'])} clusters={len(clusters)} multi={multi}",
        )
