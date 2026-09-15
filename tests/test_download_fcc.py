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


@pytest.mark.parametrize('failure', ['connect', 'read', '503'])
def test_download_retries_and_replaces_only_complete_files(tmp_path, monkeypatch, failure):
    import httpx

    dest = tmp_path / 'download.zip'
    dest.write_bytes(b'previous complete download')
    calls = []
    delays = []
    monkeypatch.setattr(fcc, 'ensure_dirs', lambda: None)
    monkeypatch.setattr(fcc, 'sleep', delays.append)

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * (1024 * 1024)
            raise httpx.ReadError('connection reset during transfer')

    def handler(request):
        calls.append(request)
        assert dest.read_bytes() == b'previous complete download'
        assert not dest.with_suffix('.zip.partial').exists()
        if len(calls) == 1:
            if failure == 'connect':
                raise httpx.ConnectError('[Errno 104] Connection reset by peer', request=request)
            if failure == 'read':
                return httpx.Response(200, stream=InterruptedStream())
            return httpx.Response(503)
        return httpx.Response(200, content=b'new complete download')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(fcc.httpx, 'stream', client.stream)
        assert fcc.download_file('https://example.test/file.zip', dest, force=True) == dest
    assert dest.read_bytes() == b'new complete download'
    assert not dest.with_suffix('.zip.partial').exists()
    assert len(calls) == 2
    assert delays == [5]


@pytest.mark.parametrize('status,expected_attempts', [(503, 4), (404, 1), (403, 1)])
def test_download_failure_is_bounded_and_preserves_existing_file(tmp_path, monkeypatch, status, expected_attempts):
    import httpx

    dest = tmp_path / 'download.zip'
    dest.write_bytes(b'previous complete download')
    calls = []
    delays = []
    monkeypatch.setattr(fcc, 'ensure_dirs', lambda: None)
    monkeypatch.setattr(fcc, 'sleep', delays.append)

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(fcc.httpx, 'stream', client.stream)
        with pytest.raises(httpx.HTTPStatusError):
            fcc.download_file('https://example.test/file.zip', dest, force=True)
    assert len(calls) == expected_attempts
    assert delays == ([5, 10, 20] if expected_attempts == 4 else [])
    assert dest.read_bytes() == b'previous complete download'
    assert not dest.with_suffix('.zip.partial').exists()
