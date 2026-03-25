from shared.arxiv import extract_best_arxiv_id_from_feed, extract_submitted_date_from_abs_html


def test_extract_best_arxiv_id_from_feed_prefers_exact_title_match():
    feed_xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.00001v1</id>
        <title>Other Paper</title>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2501.00002v2</id>
        <title>Exact Match Paper</title>
      </entry>
    </feed>
    """

    assert extract_best_arxiv_id_from_feed(feed_xml, "Exact Match Paper") == (
        "2501.00002",
        "title_search_exact",
    )


def test_extract_submitted_date_from_abs_html_reads_exact_submission_date():
    html = "<div class='submission-history'>[Submitted on 7 Jul 2024 (v1)]</div>"

    assert extract_submitted_date_from_abs_html(html) == "2024-07-07"
