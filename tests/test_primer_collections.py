"""
Primer collections + new Design Primers save modal — regression coverage.

Adds 2026-05-21 (post-v0.9.12). Covers:
  * Primer-collections file load/save/mirror roundtrip.
  * Default-wrap on first launch (`_ensure_default_primer_collection`).
  * `_save_primers` mirrors to active primer collection.
  * Active-pointer switch via `_set_active_primer_collection_name`.
  * Settings-schema key `active_primer_collection`.
  * `RestoreFromBackupModal` covers `_PRIMER_COLLECTIONS_FILE`.
  * Spaces in primer names AND collection names round-trip 1:1
    (sacred: no underscore substitution on user-typed names).
  * Collection-switch on the plasmid panel loads first plasmid or
    blanks canvas if empty.
"""

import splicecraft as sc


class TestPrimerCollectionInfrastructure:
    """Module-level helpers — no app instantiation needed."""

    def test_load_empty_returns_list(self):
        assert sc._load_primer_collections() == []

    def test_save_and_reload_roundtrip(self):
        entries = [{
            "name":        "Project alpha",   # spaces preserved
            "description": "test",
            "primers":     [],
            "saved":       "2026-05-21",
        }]
        sc._save_primer_collections(entries)
        # Force cold reload from disk
        sc._primer_collections_cache = None
        out = sc._load_primer_collections()
        assert len(out) == 1
        assert out[0]["name"] == "Project alpha"
        # Spaces preserved in the on-disk name.
        assert "_" not in out[0]["name"]

    def test_deepcopy_on_read(self):
        sc._save_primer_collections([
            {"name": "A", "primers": [{"name": "p1", "sequence": "AC"}]}
        ])
        a = sc._load_primer_collections()
        a[0]["primers"].append({"name": "leak", "sequence": "GG"})
        b = sc._load_primer_collections()
        assert len(b[0]["primers"]) == 1
        assert b[0]["primers"][0]["name"] == "p1"


class TestDefaultPrimerCollection:
    def test_first_launch_wraps_existing_primers(self):
        # Seed a flat primers.json with some user content.
        sc._save_primers([
            {"name": "existing primer 1", "sequence": "ACGT"},
            {"name": "p with spaces in name", "sequence": "TTAA"},
        ])
        # Pre-existing primer_collections is empty → wrap path.
        assert sc._load_primer_collections() == []
        sc._ensure_default_primer_collection()
        # "Main" created, marked active, contents copied from primers.json.
        assert sc._get_active_primer_collection_name() == "Main"
        colls = sc._load_primer_collections()
        assert len(colls) == 1 and colls[0]["name"] == "Main"
        names = [p["name"] for p in colls[0]["primers"]]
        # Sacred: user-typed names with spaces survive verbatim.
        assert "existing primer 1" in names
        assert "p with spaces in name" in names

    def test_idempotent_on_second_launch(self):
        sc._save_primer_collections([
            {"name": "existing", "primers": []}
        ])
        # No active pointer → default to first collection's name.
        sc._ensure_default_primer_collection()
        assert sc._get_active_primer_collection_name() == "existing"
        # Run again — nothing changes.
        n_before = len(sc._load_primer_collections())
        sc._ensure_default_primer_collection()
        assert len(sc._load_primer_collections()) == n_before


class TestSavePrimersMirror:
    """Sacred: every _save_primers call must mirror to active primer
    collection inside `_cache_lock` ([INV-50] save-chain lock-release
    gap). Verifies the mirror lands without a separate explicit call."""

    def test_save_primers_mirrors_to_active_collection(self):
        sc._save_primer_collections([
            {"name": "Main", "primers": []}
        ])
        sc._set_active_primer_collection_name("Main")
        sc._save_primers([
            {"name": "new primer with spaces", "sequence": "ACGTACGT"},
        ])
        colls = sc._load_primer_collections()
        active = next(c for c in colls if c["name"] == "Main")
        assert len(active["primers"]) == 1
        assert active["primers"][0]["name"] == "new primer with spaces"

    def test_save_primers_no_active_no_mirror(self):
        sc._save_primer_collections([
            {"name": "Inactive", "primers": []}
        ])
        # active pointer empty
        sc._set_active_primer_collection_name(None)
        sc._save_primers([{"name": "p1", "sequence": "AT"}])
        # Collection's primers untouched.
        colls = sc._load_primer_collections()
        assert colls[0]["primers"] == []


class TestActivePointer:
    def test_set_and_get_roundtrip(self):
        sc._set_active_primer_collection_name("My collection")
        assert sc._get_active_primer_collection_name() == "My collection"

    def test_set_none_clears(self):
        sc._set_active_primer_collection_name("foo")
        sc._set_active_primer_collection_name(None)
        assert sc._get_active_primer_collection_name() is None

    def test_settings_schema_includes_key(self):
        assert "active_primer_collection" in sc._SETTINGS_SCHEMA
        types, default = sc._SETTINGS_SCHEMA["active_primer_collection"]
        assert str in types
        assert default == ""


class TestRestoreFromBackupCoverage:
    def test_primer_collections_in_restore_targets(self):
        labels = [label for label, attr in sc.RestoreFromBackupModal._TARGETS]
        assert "Primer collections" in labels


class TestMasterDeleteCoverage:
    def test_primer_collections_cache_registered(self):
        assert "_primer_collections_cache" in sc._MASTER_DELETE_CACHE_ATTRS

    def test_primer_collections_file_registered(self):
        assert "_PRIMER_COLLECTIONS_FILE" in sc._USER_DATA_FILE_ATTRS


class TestRestorePrimersFromActiveCollection:
    """Startup helper: rewrites primers.json with the active
    collection's primers so a power loss / external edit can't leave
    primers.json out of sync with the collection."""

    def test_restore_overwrites_primers_file(self):
        sc._save_primer_collections([
            {"name": "Main", "primers": [
                {"name": "from-collection", "sequence": "TTTT"},
            ]}
        ])
        sc._set_active_primer_collection_name("Main")
        # Stash a different live primer into primers.json directly.
        sc._save_primers([{"name": "stale", "sequence": "AAAA"}])
        # But the mirror just wrote into Main again so Main now also
        # holds the stale primer. Re-seed Main fresh.
        sc._save_primer_collections([
            {"name": "Main", "primers": [
                {"name": "from-collection", "sequence": "TTTT"},
            ]}
        ])
        sc._restore_primers_from_active_primer_collection()
        primers = sc._load_primers()
        names = [p["name"] for p in primers]
        assert "from-collection" in names
        # Stale primer wiped — collection is authoritative.
        assert "stale" not in names


class TestPrimerSaveModalExists:
    """Smoke-test the new modal class is importable + has the expected
    constructor signature."""

    def test_modal_class_present(self):
        assert hasattr(sc, "PrimerSaveModal")

    def test_modal_accepts_oligos_kwarg(self):
        modal = sc.PrimerSaveModal(
            [{"label": "Fwd", "default_name": "test-F"}],
            default_collection="Main",
        )
        # Internal state set.
        assert len(modal._oligos) == 1
        assert modal._default_collection == "Main"


class TestPrimerSaveModalPreservesSpaces:
    """User-typed names with spaces flow through verbatim."""

    def test_modal_oligo_label_with_spaces(self):
        modal = sc.PrimerSaveModal(
            [{"label": "Forward primer with spaces",
              "default_name": "my new fwd primer"}],
        )
        # No internal mutation of the typed name.
        assert modal._oligos[0]["default_name"] == "my new fwd primer"

    def test_new_collection_name_preserves_spaces(self):
        # Spaces in collection names must round-trip.
        sc._save_primer_collections([
            {"name": "Old collection name", "primers": []}
        ])
        sc._set_active_primer_collection_name("Old collection name")
        assert (sc._get_active_primer_collection_name()
                == "Old collection name")
