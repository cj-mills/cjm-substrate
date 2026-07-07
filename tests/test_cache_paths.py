"""Cache-path tests (projected from nbs/utils/cache_paths.ipynb cell cell-tests
at the golden-reference flip): the Q3 Layer B per-(input-content, config) cache
directory helpers, end-to-end against tmp dirs."""

import time

from cjm_substrate.utils.cache_paths import (_hash_input_with_stat_cache, _MAX_STEM_LEN,
                                             _sanitize_stem, cache_dir_for_config,
                                             list_cache_entries, prune_cache_for_input)


def test_cache_dir_basic_determinism_and_config_keying(tmp_path):
    """Same (input, action, config) → same dir; different config → different dir."""
    capability_data = tmp_path / "capability_data"
    input_file = tmp_path / "podcast.mp3"
    input_file.write_bytes(b"fake mp3 content for testing")

    cfg_a = {"max_segment_duration": 300}
    cfg_b = {"max_segment_duration": 600}

    dir1 = cache_dir_for_config(capability_data, input_file, "segment_audio", cfg_a)
    dir2 = cache_dir_for_config(capability_data, input_file, "segment_audio", cfg_a)
    assert dir1 == dir2, "same config → same dir"
    assert dir1.exists(), "create=True default must mkdir"
    assert dir1.parent.name == "podcast", "stem in path"
    assert dir1.parent.parent.name == "segment_audio", "action in path"

    # Different config → different directory (the ffmpeg segment bug is fixed)
    dir3 = cache_dir_for_config(capability_data, input_file, "segment_audio", cfg_b)
    assert dir3 != dir1 and dir3.parent == dir1.parent

    dir4 = cache_dir_for_config(capability_data, input_file, "convert", cfg_a)
    assert dir4.parent.parent != dir1.parent.parent, "different action → different parent"


def test_same_stem_different_content_gets_distinct_keys(tmp_path):
    """Content hash distinguishes two same-stem files in different directories."""
    capability_data = tmp_path / "capability_data"
    (tmp_path / "podcasts").mkdir()
    (tmp_path / "lectures").mkdir()
    file_a = tmp_path / "podcasts" / "short_test_audio.mp3"
    file_b = tmp_path / "lectures" / "short_test_audio.mp3"
    file_a.write_bytes(b"podcast content")
    file_b.write_bytes(b"lecture content")

    cfg = {"sample_rate": 16000}
    dir_for_a = cache_dir_for_config(capability_data, file_a, "convert", cfg)
    dir_for_b = cache_dir_for_config(capability_data, file_b, "convert", cfg)
    assert dir_for_a != dir_for_b
    assert dir_for_a.parent.name == dir_for_b.parent.name == "short_test_audio"
    assert dir_for_a.name != dir_for_b.name  # input-hash component differs


def test_modify_in_place_changes_the_cache_key(tmp_path):
    capability_data = tmp_path / "capability_data"
    input_file = tmp_path / "audio.wav"
    input_file.write_bytes(b"original content")

    cfg = {"action": "x"}
    dir_before = cache_dir_for_config(capability_data, input_file, "x", cfg)
    # Sleep briefly so mtime advances even on coarse-resolution filesystems
    time.sleep(0.05)
    input_file.write_bytes(b"NEW content - entirely different")
    dir_after = cache_dir_for_config(capability_data, input_file, "x", cfg)
    assert dir_before != dir_after, "modify-in-place must change cache key"


def test_sequence_chaining_auto_invalidates_downstream(tmp_path):
    """When capability A's config changes, A's output content changes, so B's
    cache key auto-invalidates — chained invalidation without lineage tracking."""
    ffmpeg_data = tmp_path / "ffmpeg_data"
    voxtral_data = tmp_path / "voxtral_data"
    original_audio = tmp_path / "podcast.mp3"
    original_audio.write_bytes(b"original podcast")

    out_dir_a = cache_dir_for_config(ffmpeg_data, original_audio, "convert",
                                     {"sample_rate": 16000})
    ffmpeg_output_a = out_dir_a / "podcast.wav"
    ffmpeg_output_a.write_bytes(b"converted at 16k")

    voxtral_cfg = {"model": "voxtral-mini-3b"}
    voxtral_for_a = cache_dir_for_config(voxtral_data, ffmpeg_output_a, "execute",
                                         voxtral_cfg)

    out_dir_b = cache_dir_for_config(ffmpeg_data, original_audio, "convert",
                                     {"sample_rate": 24000})
    ffmpeg_output_b = out_dir_b / "podcast.wav"
    ffmpeg_output_b.write_bytes(b"converted at 24k - different content")

    # voxtral's own config is UNCHANGED, but its input content differs
    voxtral_for_b = cache_dir_for_config(voxtral_data, ffmpeg_output_b, "execute",
                                         voxtral_cfg)
    assert voxtral_for_a != voxtral_for_b, \
        "upstream config change must propagate to downstream cache key"


def test_create_false_returns_path_without_mkdir(tmp_path):
    capability_data = tmp_path / "capability_data"
    input_file = tmp_path / "x.wav"
    input_file.write_bytes(b"x")
    out_dir = cache_dir_for_config(capability_data, input_file, "act", {"k": 1},
                                   create=False)
    assert not out_dir.exists()


def test_hash_input_content_false_uses_the_path_string(tmp_path):
    """hash_input_content=False hashes the path string (URL / non-file inputs)."""
    capability_data = tmp_path / "capability_data"
    cfg = {"x": 1}
    dir1 = cache_dir_for_config(capability_data, "https://example.com/audio-A.mp3",
                                "fetch", cfg, hash_input_content=False)
    dir2 = cache_dir_for_config(capability_data, "https://example.com/audio-B.mp3",
                                "fetch", cfg, hash_input_content=False)
    assert dir1 != dir2
    dir1_again = cache_dir_for_config(capability_data, "https://example.com/audio-A.mp3",
                                      "fetch", cfg, hash_input_content=False)
    assert dir1 == dir1_again


def test_sanitize_stem_edge_cases():
    assert _sanitize_stem("normal.mp3") == "normal"
    assert _sanitize_stem("01 - Chapter 1.mp3") == "01 - Chapter 1"
    s = _sanitize_stem("weird<>:|?*.mp3")
    assert "<" not in s and ">" not in s and "|" not in s
    assert len(_sanitize_stem("x" * 500 + ".mp3")) <= _MAX_STEM_LEN
    assert _sanitize_stem(".") == "_"  # degenerate path
    assert _sanitize_stem("  ..hello..  .mp3") == "hello"  # Windows dot/space rule


def test_list_and_prune_companions(tmp_path):
    capability_data = tmp_path / "capability_data"
    input_file = tmp_path / "audio.wav"
    input_file.write_bytes(b"x")

    d1 = cache_dir_for_config(capability_data, input_file, "act", {"k": 1})
    d2 = cache_dir_for_config(capability_data, input_file, "act", {"k": 2})
    d3 = cache_dir_for_config(capability_data, input_file, "act", {"k": 3})
    for d, marker in ((d1, "1"), (d2, "2"), (d3, "3")):
        (d / "marker").write_text(marker)

    assert set(list_cache_entries(capability_data, input_file, "act")) == {d1, d2, d3}

    # Dry-run prune reports without touching the filesystem
    would_delete = prune_cache_for_input(capability_data, input_file, "act",
                                         keep={d2}, dry_run=True)
    assert set(would_delete) == {d1, d3}
    assert d1.exists() and d2.exists() and d3.exists()

    deleted = prune_cache_for_input(capability_data, input_file, "act", keep={d2})
    assert set(deleted) == {d1, d3}
    assert not d1.exists() and not d3.exists() and d2.exists()
    assert list_cache_entries(capability_data, input_file, "act") == [d2]


def test_stat_cache_round_trip(tmp_path):
    cache_db = tmp_path / "cache.db"
    input_file = tmp_path / "test.bin"
    input_file.write_bytes(b"deterministic content for hashing")

    h1 = _hash_input_with_stat_cache(input_file, cache_path=cache_db)  # cold
    assert cache_db.exists(), "cache DB should have been created"
    assert _hash_input_with_stat_cache(input_file, cache_path=cache_db) == h1  # warm

    time.sleep(0.05)
    input_file.write_bytes(b"DIFFERENT content")
    h3 = _hash_input_with_stat_cache(input_file, cache_path=cache_db)
    assert h3 != h1, "modify-in-place must produce different hash"
    assert _hash_input_with_stat_cache(input_file, cache_path=cache_db) == h3


def test_skip_cache_bypasses_the_lookup(tmp_path):
    cache_db = tmp_path / "cache.db"
    input_file = tmp_path / "test.bin"
    input_file.write_bytes(b"content")
    h1 = _hash_input_with_stat_cache(input_file, cache_path=cache_db)
    h2 = _hash_input_with_stat_cache(input_file, cache_path=cache_db, skip_cache=True)
    assert h1 == h2
