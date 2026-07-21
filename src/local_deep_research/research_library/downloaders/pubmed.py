"""
PubMed/PMC PDF Downloader
"""

import re
import time
from typing import Optional
from urllib.parse import urlparse
from loguru import logger

from .base import BaseDownloader, ContentType, DownloadResult


class PubMedDownloader(BaseDownloader):
    """Downloader for PubMed and PubMed Central articles with PDF and text support."""

    def __init__(self, timeout: int = 30, rate_limit_delay: float = 1.0):
        """
        Initialize PubMed downloader.

        Args:
            timeout: Request timeout in seconds
            rate_limit_delay: Delay between requests to avoid rate limiting
        """
        super().__init__(timeout)
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time: float = 0.0

    def can_handle(self, url: str) -> bool:
        """Check if URL is from PubMed or PMC."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return False

            # Check for pubmed.ncbi.nlm.nih.gov
            if hostname == "pubmed.ncbi.nlm.nih.gov":
                return True

            # Check for ncbi.nlm.nih.gov with /pmc in path
            if hostname == "ncbi.nlm.nih.gov" and "/pmc" in parsed.path:
                return True

            # Check for europepmc.org and its subdomains
            if hostname == "europepmc.org" or hostname.endswith(
                ".europepmc.org"
            ):
                return True

            return False
        except Exception:
            return False

    def download(
        self, url: str, content_type: ContentType = ContentType.PDF
    ) -> Optional[bytes]:
        """Download content from PubMed/PMC."""
        # Apply rate limiting
        self._apply_rate_limit()

        if content_type == ContentType.TEXT:
            # Try to get full text from API
            return self._download_text(url)
        # Download PDF
        return self._download_pdf_content(url)

    def download_with_result(
        self, url: str, content_type: ContentType = ContentType.PDF
    ) -> DownloadResult:
        """Download content and return detailed result with skip reason."""
        # Apply rate limiting
        self._apply_rate_limit()

        if content_type == ContentType.TEXT:
            content = self._download_text(url)
            if content:
                return DownloadResult(content=content, is_success=True)
            return DownloadResult(
                skip_reason="Full text not available - may require subscription"
            )
        # Try to download PDF with detailed tracking
        return self._download_pdf_with_result(url)

    def _download_pdf_content(self, url: str) -> Optional[bytes]:
        """Download PDF from PubMed/PMC."""
        # Handle different URL types
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        path = parsed.path or ""

        # Check for PMC article direct download
        if hostname == "ncbi.nlm.nih.gov" and "/pmc/articles/PMC" in path:
            return self._download_pmc_direct(url)
        # Check for PubMed main site
        if hostname == "pubmed.ncbi.nlm.nih.gov":
            return self._download_pubmed(url)
        # Check for Europe PMC and subdomains
        if hostname == "europepmc.org" or hostname.endswith(".europepmc.org"):
            return self._download_europe_pmc(url)

        return None

    def _download_pdf_with_result(self, url: str) -> DownloadResult:
        """Download PDF and return detailed result with skip reason."""
        # Handle different URL types
        if "/pmc/articles/PMC" in url:
            pmc_match = re.search(r"(PMC\d+)", url)
            if not pmc_match:
                return DownloadResult(skip_reason="Invalid PMC URL format")

            pmc_id = pmc_match.group(1)
            logger.info(f"Downloading PMC article: {pmc_id}")

            # Quick index precheck: EuropePMC only hosts full text for articles
            # it indexes. For PMC ids that exist only in NCBI (common for very
            # recent articles), the fullTextXML call below 404s after ~3s, then
            # NCBI PDF endpoints each retry for ~13s. Checking the index first
            # (0.5s) lets us skip straight to NCBI for those ids.
            in_europe_pmc = self._is_in_europe_pmc(pmc_id)

            if in_europe_pmc is not False:
                # Try Europe PMC fullTextXML (replaces the dead ptpmcrender.fcgi
                # PDF endpoint, which now returns an empty reply for every id).
                text_content = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
                if text_content:
                    return DownloadResult(
                        content=text_content.encode("utf-8", errors="ignore"),
                        is_success=True,
                    )

            # Fall back to NCBI PMC (legacy PDF endpoints; often 403/PoW-protected now)
            pdf_content = self._download_via_ncbi_pmc(pmc_id)
            if pdf_content:
                return DownloadResult(content=pdf_content, is_success=True)

            # Distinguish "not in Europe PMC index" from "paywalled" so the
            # caller can classify the failure correctly.
            if in_europe_pmc is False:
                return DownloadResult(
                    skip_reason=f"PMC article {pmc_id} not in Europe PMC - not accessible via open access"
                )
            return DownloadResult(
                skip_reason=f"PMC article {pmc_id} not accessible - may be retracted or embargoed"
            )

        if urlparse(url).hostname == "pubmed.ncbi.nlm.nih.gov":
            # Extract PMID
            pmid_match = re.search(r"/(\d+)/?", url)
            if not pmid_match:
                return DownloadResult(skip_reason="Invalid PubMed URL format")

            pmid = pmid_match.group(1)
            logger.info(f"Processing PubMed article: {pmid}")

            # Check if article is open access via Europe PMC
            try:
                api_url = (
                    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                )
                params = {"query": f"EXT_ID:{pmid}", "format": "json"}

                response = self.session.get(api_url, params=params, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("resultList", {}).get("result", [])

                    if results:
                        article = results[0]

                        # Check if article exists but is not open access
                        if article.get("isOpenAccess") != "Y":
                            journal = article.get(
                                "journalTitle", "Unknown journal"
                            )
                            return DownloadResult(
                                skip_reason=f"Article requires subscription to {journal}"
                            )

                        # Check if PDF is available
                        if article.get("hasPDF") != "Y":
                            return DownloadResult(
                                skip_reason="No PDF version available for this article"
                            )

                        # Try to download
                        pmcid = article.get("pmcid")
                        if pmcid:
                            text_content = (
                                self._fetch_fulltext_xml_from_europe_pmc(pmcid)
                            )
                            if text_content:
                                return DownloadResult(
                                    content=text_content.encode(
                                        "utf-8", errors="ignore"
                                    ),
                                    is_success=True,
                                )
                    else:
                        return DownloadResult(
                            skip_reason=f"Article PMID:{pmid} not found in Europe PMC database"
                        )
            except Exception as e:
                logger.debug(f"Error checking article status: {e}")

            # Try to find PMC ID via NCBI
            pmc_id = self._get_pmc_id_from_pmid(pmid)
            if pmc_id:
                logger.info(f"Found PMC ID: {pmc_id} for PMID: {pmid}")

                # Try downloading via Europe PMC fullTextXML
                text_content = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
                if text_content:
                    return DownloadResult(
                        content=text_content.encode("utf-8", errors="ignore"),
                        is_success=True,
                    )

                # Fall back to NCBI PMC PDF endpoints
                pdf_content = self._download_via_ncbi_pmc(pmc_id)
                if pdf_content:
                    return DownloadResult(content=pdf_content, is_success=True)

                return DownloadResult(
                    skip_reason=f"PMC version exists but PDF not accessible (PMC ID: {pmc_id})"
                )

            return DownloadResult(
                skip_reason="No free full-text available - article may be paywalled"
            )

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname == "europepmc.org" or hostname.endswith(".europepmc.org"):
            pmc_match = re.search(r"(PMC\d+)", url)
            if pmc_match:
                pmc_id = pmc_match.group(1)
                text_content = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
                if text_content:
                    return DownloadResult(
                        content=text_content.encode("utf-8", errors="ignore"),
                        is_success=True,
                    )
                return DownloadResult(
                    skip_reason=f"Europe PMC article {pmc_id} not accessible"
                )
            return DownloadResult(skip_reason="Invalid Europe PMC URL format")
        return DownloadResult(skip_reason="Unsupported PubMed/PMC URL format")

    def _download_text(self, url: str) -> Optional[bytes]:
        """Download full text content from PubMed/PMC APIs."""
        # Extract PMID or PMC ID
        pmid = None
        pmc_id = None

        parsed_url = urlparse(url)
        if parsed_url.hostname == "pubmed.ncbi.nlm.nih.gov":
            pmid_match = re.search(r"/(\d+)/?", url)
            if pmid_match:
                pmid = pmid_match.group(1)
        elif "/pmc/articles/PMC" in url:
            pmc_match = re.search(r"(PMC\d+)", url)
            if pmc_match:
                pmc_id = pmc_match.group(1)

        # Try Europe PMC API for full text
        if pmid or pmc_id:
            text = self._fetch_text_from_europe_pmc(pmid, pmc_id)
            if text:
                return text.encode("utf-8")

        # Fallback: Download PDF and extract text
        pdf_content = self._download_pdf_content(url)
        if pdf_content:
            text = self.extract_text_from_pdf(pdf_content)
            if text:
                return text.encode("utf-8")

        return None

    def _fetch_text_from_europe_pmc(
        self, pmid: Optional[str], pmc_id: Optional[str]
    ) -> Optional[str]:
        """Fetch full text from Europe PMC API."""
        try:
            # Construct query
            if pmc_id:
                query = f"PMC:{pmc_id.replace('PMC', '')}"
            elif pmid:
                query = f"EXT_ID:{pmid}"
            else:
                return None

            # Get article metadata first
            api_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            params = {
                "query": query,
                "format": "json",
                "resultType": "core",  # Get more detailed results
            }

            response = self.session.get(api_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                results = data.get("resultList", {}).get("result", [])

                if results and results[0].get("isOpenAccess") == "Y":
                    article = results[0]
                    # Try to get full text XML
                    if article.get("pmcid"):
                        fulltext_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{article['pmcid']}/fullTextXML"
                        text_response = self.session.get(
                            fulltext_url, timeout=30
                        )

                        if text_response.status_code == 200:
                            # Extract text from XML (simple approach - just get text content)
                            import re

                            xml_content = text_response.text
                            # Remove XML tags to get plain text
                            text = re.sub(r"<[^>]+>", " ", xml_content)
                            text = " ".join(text.split())

                            if text:
                                logger.info(
                                    "Retrieved full text from Europe PMC API"
                                )
                                return text

        except Exception as e:
            logger.debug(f"Failed to fetch text from Europe PMC: {e}")

        return None

    def _apply_rate_limit(self):
        """Apply rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _download_pmc_direct(self, url: str) -> Optional[bytes]:
        """Download directly from PMC URL."""
        pmc_match = re.search(r"(PMC\d+)", url)
        if not pmc_match:
            return None

        pmc_id = pmc_match.group(1)
        logger.info(f"Downloading PMC article: {pmc_id}")

        # Try Europe PMC fullTextXML first (more reliable)
        text = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
        if text:
            return text.encode("utf-8", errors="ignore")

        # Fallback to NCBI PMC
        return self._download_via_ncbi_pmc(pmc_id)

    def _download_pubmed(self, url: str) -> Optional[bytes]:
        """Download from PubMed URL."""
        # Extract PMID
        pmid_match = re.search(r"/(\d+)/?", url)
        if not pmid_match:
            return None

        pmid = pmid_match.group(1)
        logger.info(f"Processing PubMed article: {pmid}")

        # Try Europe PMC API first
        text = self._try_europe_pmc_api(pmid)
        if text:
            return text

        # Try to find PMC ID via NCBI API
        pmc_id = self._get_pmc_id_from_pmid(pmid)
        if pmc_id:
            logger.info(f"Found PMC ID: {pmc_id} for PMID: {pmid}")

            # Try Europe PMC fullTextXML with PMC ID
            text = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
            if text:
                return text.encode("utf-8", errors="ignore")

            # Try NCBI PMC
            pdf_content = self._download_via_ncbi_pmc(pmc_id)
            if pdf_content:
                return pdf_content

        logger.info(f"No PMC version available for PMID: {pmid}")
        return None

    def _download_europe_pmc(self, url: str) -> Optional[bytes]:
        """Download from Europe PMC URL."""
        # Extract PMC ID from URL
        pmc_match = re.search(r"(PMC\d+)", url)
        if pmc_match:
            pmc_id = pmc_match.group(1)
            return self._download_via_europe_pmc(pmc_id)
        return None

    def _try_europe_pmc_api(self, pmid: str) -> Optional[bytes]:
        """Try downloading via Europe PMC API using PMID.

        Returns UTF-8 encoded full-text bytes when an open-access article with
        a PMCID is found, otherwise ``None``.
        """
        try:
            # Query Europe PMC API
            api_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            params = {"query": f"EXT_ID:{pmid}", "format": "json"}

            response = self.session.get(api_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                results = data.get("resultList", {}).get("result", [])

                if results:
                    article = results[0]
                    # Check if article is open access with full text
                    if article.get("isOpenAccess") == "Y":
                        pmcid = article.get("pmcid")
                        if pmcid:
                            logger.info(
                                f"Found open access article via Europe PMC API: {pmcid}"
                            )
                            text = self._fetch_fulltext_xml_from_europe_pmc(
                                pmcid
                            )
                            if text:
                                return text.encode("utf-8", errors="ignore")

        except Exception as e:
            logger.debug(f"Europe PMC API query failed: {e}")

        return None

    def _get_pmc_id_from_pmid(self, pmid: str) -> Optional[str]:
        """Convert PMID to PMC ID using NCBI E-utilities."""
        try:
            # Use NCBI E-utilities to find PMC ID
            elink_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
            )
            params = {
                "dbfrom": "pubmed",
                "db": "pmc",
                "id": pmid,
                "retmode": "json",
            }

            response = self.session.get(elink_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                link_sets = data.get("linksets", [])

                if link_sets and "linksetdbs" in link_sets[0]:
                    for linksetdb in link_sets[0]["linksetdbs"]:
                        if linksetdb.get("dbto") == "pmc" and linksetdb.get(
                            "links"
                        ):
                            pmc_id_num = linksetdb["links"][0]
                            return f"PMC{pmc_id_num}"

        except Exception as e:
            logger.debug(f"NCBI E-utilities lookup failed: {e}")

        # Fallback: Try scraping the PubMed page
        try:
            response = self.session.get(
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", timeout=10
            )

            if response.status_code == 200:
                pmc_match = re.search(r"PMC\d+", response.text)
                if pmc_match:
                    return pmc_match.group(0)

        except Exception as e:
            logger.debug(f"PubMed page scraping failed: {e}")

        return None

    def _fetch_fulltext_xml_from_europe_pmc(
        self, pmc_id: str
    ) -> Optional[str]:
        """Fetch full-text XML from Europe PMC and return it as plain text.

        The former ``ptpmcrender.fcgi`` PDF endpoint is dead (returns an empty
        reply for every id), but the REST API ``fullTextXML`` endpoint works for
        open-access articles indexed in Europe PMC. We fetch the XML and strip
        tags to plain text — callers treat the UTF-8 bytes as document content.
        Returns ``None`` when the article is not in Europe PMC or has no
        full-text XML (e.g. paywalled / not OA).
        """
        # pmc_id may be "PMC12345" or "12345"; normalize to the bare number for
        # the REST path, which expects the PMC id without the "PMC" prefix.
        pmc_num = pmc_id.replace("PMC", "")
        xml_url = (
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/PMC{pmc_num}"
            f"/fullTextXML"
        )
        logger.debug(f"Trying Europe PMC fullTextXML: {xml_url}")
        try:
            response = self.session.get(xml_url, timeout=30)
        except Exception as e:
            logger.debug(f"Europe PMC fullTextXML request failed: {e}")
            return None

        if response.status_code != 200:
            # 404 = article not in Europe PMC or no full text available.
            logger.debug(
                f"Europe PMC fullTextXML for {pmc_id} returned {response.status_code}"
            )
            return None

        xml_content = response.text
        if not xml_content or "<" not in xml_content:
            return None

        # Strip XML tags to get plain text (same approach as the existing
        # _fetch_text_from_europe_pmc helper).
        text = re.sub(r"<[^>]+>", " ", xml_content)
        text = " ".join(text.split())
        if not text:
            return None

        # Europe PMC returns a 200 with an ``article-type="advert"`` stub whose
        # entire body is a sentence like "The content is available as a PDF
        # (937.3 KB)." for articles it indexes but has no extractable full text
        # for (e.g. scanned historical issues). That is not usable document
        # text, so treat it as a miss and fall through to NCBI.
        lowered = text.lower()
        if (
            "<advert" in xml_content.lower()
            or 'article-type="advert"' in xml_content.lower()
        ) and "available as a pdf" in lowered:
            logger.debug(
                f"Europe PMC fullTextXML for {pmc_id} is a PDF-only stub, skipping"
            )
            return None

        logger.info(
            f"Retrieved full text from Europe PMC fullTextXML: {pmc_id}"
        )
        return text

    def _is_in_europe_pmc(self, pmc_id: str) -> Optional[bool]:
        """Whether ``pmc_id`` is indexed in Europe PMC.

        Returns ``True``/``False`` from the search API, or ``None`` if the
        lookup itself failed (so callers can't draw a conclusion either way).
        """
        try:
            response = self.session.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                # ``pmcid:`` is an exact field match on the PMC id. The looser
                # ``PMC:`` form is a full-text search and matches unrelated
                # articles whose text merely contains the digits.
                params={"query": f"pmcid:{pmc_id}", "format": "json"},
                timeout=10,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            return (data.get("hitCount", 0) or 0) > 0
        except Exception as e:
            logger.debug(f"Europe PMC index lookup failed: {e}")
            return None

    def _download_via_europe_pmc(self, pmc_id: str) -> Optional[bytes]:
        """Download full text from Europe PMC.

        Returns UTF-8 encoded text derived from the Europe PMC ``fullTextXML``
        REST endpoint. The legacy ``ptpmcrender.fcgi`` PDF URL is no longer used
        — it returns an empty reply for every id.
        """
        text = self._fetch_fulltext_xml_from_europe_pmc(pmc_id)
        if text:
            logger.info(f"Successfully downloaded from Europe PMC: {pmc_id}")
            return text.encode("utf-8", errors="ignore")
        return None

    def _download_via_ncbi_pmc(self, pmc_id: str) -> Optional[bytes]:
        """Download PDF via NCBI PMC.

        NCBI migrated PMC to ``pmc.ncbi.nlm.nih.gov`` and now protects PDF
        downloads with a Proof-of-Work challenge, so these endpoints frequently
        return 403 or an interstitial HTML page. We try both the new and legacy
        host patterns as a best-effort fallback; success is not guaranteed.
        """
        pmc_num = pmc_id.replace("PMC", "")
        # Try both the new pmc.ncbi.nlm.nih.gov host and the legacy
        # www.ncbi.nlm.nih.gov host. The /pdf/ directory form redirects to the
        # actual PDF filename on the new host.
        url_patterns = [
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf/",
            f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/main.pdf",
        ]

        for pdf_url in url_patterns:
            logger.debug(f"Trying NCBI PMC: {pdf_url}")

            # Add referer header for NCBI
            headers = {
                "Referer": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/"
            }

            pdf_content = self._download_pdf(pdf_url, headers)
            if pdf_content:
                logger.info(f"Successfully downloaded from NCBI PMC: {pmc_id}")
                return pdf_content

        return None
