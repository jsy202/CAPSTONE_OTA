"""HTTP proof must bind successful responses to every required OTA file."""
from bench.evidence import analyze_http


def test_http_analysis_requires_matching_success_for_each_file():
    rows = ('1\t10.77.0.2\t10.77.0.1\tGET\t/token/release.json\t\t\n'
            '2\t10.77.0.1\t10.77.0.2\t\t\t200\t1\n'
            '3\t10.77.0.2\t10.77.0.1\tGET\t/token/app.tar.gz\t\t\n'
            '4\t10.77.0.1\t10.77.0.2\t\t\t404\t3\n'
            '5\t10.77.0.1\t10.77.0.2\t\t\t200\t999\n')
    result = analyze_http(rows)
    assert result['successful_files'] == {'release.json': 1, 'release.sig': 0, 'app.tar.gz': 0}
