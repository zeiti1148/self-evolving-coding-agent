"""Validate the fixed evaluation corpus, without model calls or Maven side effects."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "benchmarks/catalog.json").read_text(encoding="utf-8"))
IDS = {"java-null-greeting", "java-null-profile", "java-null-list", "java-null-map",
       "java-range-end", "java-parse-count"}
NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def test_catalog_has_six_unique_fixed_cases():
    assert isinstance(CATALOG, list) and len(CATALOG) == 6
    assert {case["id"] for case in CATALOG} == IDS
    assert len({case["path"] for case in CATALOG}) == 6
    assert {case["error_type"] for case in CATALOG} == {
        "NullPointerException", "ArrayIndexOutOfBoundsException", "NumberFormatException"
    }


@pytest.mark.parametrize("case", CATALOG, ids=lambda case: case["id"])
def test_case_metadata_points_to_complete_maven_project(case):
    assert set(case) == {"id", "path", "task_description", "error_type", "expected_test_count"}
    assert all(isinstance(case[field], str) and case[field].strip() for field in
               ("id", "path", "task_description", "error_type"))
    assert type(case["expected_test_count"]) is int and 1 <= case["expected_test_count"] <= 5
    assert case["error_type"] in case["task_description"]
    relative = PurePosixPath(case["path"])
    assert not relative.is_absolute() and ".." not in relative.parts and "\\" not in case["path"]
    project = ROOT / relative
    assert project.resolve().is_relative_to((ROOT / "benchmarks").resolve())
    assert project.is_dir() and (project / "pom.xml").is_file()
    sources = list((project / "src/main/java").rglob("*.java"))
    tests = list((project / "src/test/java").rglob("*Test.java"))
    assert sources and tests
    test_source = "\n".join(path.read_text(encoding="utf-8") for path in tests)
    assert len(re.findall(r"@Test\b", test_source)) == case["expected_test_count"]
    assert "@Disabled" not in test_source and "assertEquals(" in test_source


@pytest.mark.parametrize("case", CATALOG, ids=lambda case: case["id"])
def test_maven_versions_are_consistent_across_cases(case):
    pom = ET.parse(ROOT / case["path"] / "pom.xml").getroot()
    assert pom.findtext("m:properties/m:maven.compiler.release", namespaces=NS) == "17"
    assert pom.findtext("m:properties/m:project.build.sourceEncoding", namespaces=NS) == "UTF-8"
    plugins = {node.findtext("m:artifactId", namespaces=NS): node.findtext("m:version", namespaces=NS)
               for node in pom.findall("m:build/m:plugins/m:plugin", NS)}
    assert plugins["maven-compiler-plugin"] == "3.14.1"
    assert plugins["maven-surefire-plugin"] == "3.2.5"
    junit = next(node for node in pom.findall("m:dependencies/m:dependency", NS)
                 if node.findtext("m:artifactId", namespaces=NS) == "junit-jupiter")
    version = junit.findtext("m:version", namespaces=NS)
    if version == "${junit.version}":
        version = pom.findtext("m:properties/m:junit.version", namespaces=NS)
    assert version == "5.10.2"
    assert junit.findtext("m:scope", namespaces=NS) == "test"
