"""test_protein_collections — the protein-collections persistence subsystem
that backs the Operon Design workbench (named libraries of protein
sequences a user adds / renames / deletes).

The autouse `_protect_user_data` fixture (conftest.py) sandboxes every
user-data JSON to tmp_path and authorises writes, so the `_save_*` calls
here are safe and never touch the real data dir.
"""
import splicecraft as sc


class TestProteinCollectionsPersistence:
    def test_add_and_reload_from_disk(self):
        assert sc._protein_collection_add("Lux", "luxC", "MKFGLFFL") is None
        sc._state._protein_collections_cache = None                  # force a disk reload
        cols = sc._load_protein_collections()
        assert len(cols) == 1 and cols[0]["name"] == "Lux"
        p = cols[0]["proteins"][0]
        assert p["name"] == "luxC" and p["sequence"] == "MKFGLFFL"

    def test_add_overwrites_by_name(self):
        sc._protein_collection_add("C", "p", "MKL")
        sc._protein_collection_add("C", "p", "MWWW")
        prots = sc._load_protein_collections()[0]["proteins"]
        assert len(prots) == 1 and prots[0]["sequence"] == "MWWW"

    def test_multiple_collections(self):
        sc._protein_collection_add("A", "p1", "MK")
        sc._protein_collection_add("B", "p2", "ML")
        assert {c["name"] for c in sc._load_protein_collections()} == {"A", "B"}

    def test_remove_protein(self):
        sc._protein_collection_add("C", "p1", "MK")
        sc._protein_collection_add("C", "p2", "ML")
        assert sc._protein_collection_remove("C", "p1") is None
        kept = [p["name"] for p in sc._load_protein_collections()[0]["proteins"]]
        assert kept == ["p2"]
        assert sc._protein_collection_remove("C", "ghost")    # error string
        assert sc._protein_collection_remove("ghost", "p2")   # error string

    def test_delete_collection(self):
        sc._protein_collection_add("A", "p", "MK")
        sc._protein_collection_add("B", "p", "MK")
        assert sc._protein_collection_delete("A") is None
        assert {c["name"] for c in sc._load_protein_collections()} == {"B"}
        assert sc._protein_collection_delete("A")             # already gone -> error

    def test_rename_collection(self):
        sc._protein_collection_add("Old", "p", "MK")
        assert sc._protein_collection_rename("Old", "New") is None
        assert {c["name"] for c in sc._load_protein_collections()} == {"New"}
        sc._protein_collection_add("X", "p", "MK")
        assert sc._protein_collection_rename("New", "X")      # dup target -> error
        assert sc._protein_collection_rename("Ghost", "Y")    # missing source -> error

    def test_validation_errors_dont_write(self):
        assert sc._protein_collection_add("", "p", "MK")      # empty collection name
        assert sc._protein_collection_add("C", "", "MK")      # empty protein name
        assert sc._protein_collection_add("C", "p", "")       # empty sequence
        assert sc._protein_collection_add("C", "p", "MKBZ")   # non-canonical AA (B, Z)
        assert sc._load_protein_collections() == []           # nothing persisted

    def test_load_returns_deepcopy(self):
        sc._protein_collection_add("A", "p", "MK")
        a = sc._load_protein_collections()
        a[0]["proteins"][0]["sequence"] = "MUTATED"           # mutate the returned copy
        b = sc._load_protein_collections()
        assert b[0]["proteins"][0]["sequence"] == "MK"        # cache not poisoned


class TestProteinCollectionsRegistration:
    """Parity: the new persisted file is registered in every required
    registry (matching the HMM-catalog / plasmid-collections precedent),
    so master-delete, backup/restore, and the agent API all cover it."""

    def test_user_data_file_attrs(self):
        assert "_PROTEIN_COLLECTIONS_FILE" in sc._USER_DATA_FILE_ATTRS

    def test_master_delete_caches(self):
        assert "_protein_collections_cache" in sc._MASTER_DELETE_CACHE_ATTRS

    def test_agent_backup_labels(self):
        assert (sc._AGENT_BACKUP_LABELS.get("protein_collections")
                == "_PROTEIN_COLLECTIONS_FILE")

    def test_restore_targets(self):
        attrs = [a for _label, a in sc.RestoreFromBackupModal._TARGETS]
        assert "_PROTEIN_COLLECTIONS_FILE" in attrs
