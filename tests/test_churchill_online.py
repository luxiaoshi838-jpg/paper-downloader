import unittest
import requests

DOIS = [
    "10.1016/j.margeo.2020.106154",
    "10.1016/j.chemosphere.2016.08.008",
    "10.5194/os-17-1367-2021",
    "10.1016/j.ecss.2007.09.030",
    "10.1029/2005JD006173",
    "10.1139/f80-143",
    "10.1007/s10533-017-0388-8",
    "10.1016/j.ejrh.2025.102585",
    "10.2307/1552532",
    "10.1007/s00382-019-04731-2",
    "10.1080/07055900.2015.1026872",
    "10.1021/es803138z",
    "10.1016/j.dendro.2018.05.004",
    "10.1021/acs.est.6b00365",
    "10.1002/wrcr.20117",
    "10.1016/S0022-1694(97)00073-5",
    "10.4296/cwrj2011-923",
    "10.1007/s00300-021-02989-z",
    "10.3808/jei.201100199",
    "10.1002/joc.1362",
    "10.4081/jlimnol.2010.s1.33",
    "10.1016/j.ejrh.2023.101391",
    "10.1016/j.marchem.2008.08.001",
    "10.1016/j.jhydrol.2023.129820",
    "10.1016/j.jhydrol.2020.125876",
    "10.1080/07011784.2014.985512",
    "10.3390/w16182648",
    "10.1080/07011784.2025.2509226",
]


class ChurchillOnlineDiagnostics(unittest.TestCase):
    def test_metadata_sources(self):
        session = requests.Session()
        session.headers.update({
            "User-Agent": "paper-downloader-diagnostic/1.0 (https://github.com/luxiaoshi838-jpg/paper-downloader)"
        })
        print("DIAG_BEGIN", flush=True)
        for index, doi in enumerate(DOIS, 1):
            row = {"n": index, "doi": doi}
            try:
                r = session.get(
                    f"https://api.unpaywall.org/v2/{requests.utils.quote(doi, safe='')}",
                    params={"email": "luxiaoshi838-jpg@users.noreply.github.com"},
                    timeout=30,
                )
                row["up_status"] = r.status_code
                if r.ok:
                    j = r.json()
                    row["up_oa"] = bool(j.get("is_oa"))
                    best = j.get("best_oa_location") or {}
                    row["up_pdf"] = bool(best.get("url_for_pdf"))
                    row["up_host"] = best.get("host_type") or ""
                else:
                    row["up_error"] = r.text[:120].replace("\n", " ")
            except Exception as exc:
                row["up_error"] = type(exc).__name__ + ":" + str(exc)[:100]

            try:
                r = session.get(
                    "https://api.openalex.org/works/"
                    + requests.utils.quote(f"https://doi.org/{doi}", safe=""),
                    timeout=30,
                )
                row["oa_status"] = r.status_code
                if r.ok:
                    j = r.json()
                    row["oa_is_oa"] = bool((j.get("open_access") or {}).get("is_oa"))
                    best = j.get("best_oa_location") or {}
                    row["oa_pdf"] = bool(best.get("pdf_url"))
                else:
                    row["oa_error"] = r.text[:120].replace("\n", " ")
            except Exception as exc:
                row["oa_error"] = type(exc).__name__ + ":" + str(exc)[:100]

            try:
                r = session.get(
                    f"https://api.crossref.org/works/{requests.utils.quote(doi, safe='')}",
                    timeout=30,
                )
                row["cr_status"] = r.status_code
                if r.ok:
                    links = ((r.json().get("message") or {}).get("link") or [])
                    row["cr_links"] = len(links)
                    row["cr_pdf"] = sum(
                        "pdf" in str(x.get("content-type", "")).lower() for x in links
                    )
                else:
                    row["cr_error"] = r.text[:120].replace("\n", " ")
            except Exception as exc:
                row["cr_error"] = type(exc).__name__ + ":" + str(exc)[:100]

            print("DIAG", row, flush=True)
        print("DIAG_END", flush=True)
        self.assertEqual(len(DOIS), 28)


if __name__ == "__main__":
    unittest.main()
