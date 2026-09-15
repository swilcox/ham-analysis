"""Verify paired FCC releases without network access."""

import zipfile

import pytest

from ham_analysis import download_fcc as fcc


def _archive(path, day, names):
    with zipfile.ZipFile(path, 'w') as z:
        for name in names:
            entry = zipfile.ZipInfo(name, (2026, 9, day, 0, 0, 0))
            z.writestr(entry, f'{name} release {day}')


def test_missing_application_cache_refreshes_both_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(fcc, 'FCC_RAW_DIR', tmp_path)
    monkeypatch.setattr(fcc, 'ensure_dirs', lambda: None)
    _archive(tmp_path / 'l_amat.zip', 1, ['hd.dat', 'en.dat', 'am.dat'])
    old_extract = tmp_path / 'extract'
    old_extract.mkdir()
    (old_extract / 'HD.dat').write_text('stale')
    calls = []

    def download(url, dest, *, force=False):
        calls.append((dest.name, force))
        names = ['hd.dat', 'en.dat', 'ad.dat' if dest.name == 'a_amat.zip' else 'am.dat']
        _archive(dest, 12 if dest.name == 'a_amat.zip' else 13, names)
        return dest

    monkeypatch.setattr(fcc, 'download_file', download)
    paths = fcc.download_fcc()
    assert calls == [('l_amat.zip', True), ('a_amat.zip', True)]
    assert paths['HD.dat'].read_text() == 'hd.dat release 13'
    assert paths['application_AD.dat'].read_text() == 'ad.dat release 12'
    assert paths['application_HD.dat'] != paths['HD.dat']


def test_mismatched_weekly_releases_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(fcc, 'FCC_RAW_DIR', tmp_path)
    monkeypatch.setattr(fcc, 'ensure_dirs', lambda: None)
    monkeypatch.setattr(fcc, 'download_file', lambda url, dest, **kwargs: dest)
    _archive(tmp_path / 'l_amat.zip', 13, ['HD.dat', 'EN.dat', 'AM.dat'])
    _archive(tmp_path / 'a_amat.zip', 5, ['HD.dat', 'EN.dat', 'AD.dat'])
    with pytest.raises(ValueError, match='more than two days'):
        fcc.download_fcc()
    assert not (tmp_path / 'extract').exists()
