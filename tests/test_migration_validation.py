from scripts.migrate_meta_to_supabase import find_orphans


def test_find_orphans_detects_dangling_links():
    clients = {1, 2}
    domains = {10, 11}
    client_domains = [(1, 10), (3, 10), (1, 99)]  # client 3 and domain 99 are orphans
    orphans = find_orphans(clients, domains, client_domains)
    assert (3, 10) in orphans["client_domains_bad_client"]
    assert (1, 99) in orphans["client_domains_bad_domain"]


def test_find_orphans_clean_set_is_empty():
    orphans = find_orphans(
        clients={1},
        domains={10},
        client_domains=[(1, 10)],
        projects=[(5, 1)],
        project_domains=[(5, 10)],
        client_datasets=[(1, 10, "analytics_1"), (1, None, "gsc_1")],
    )
    assert all(len(v) == 0 for v in orphans.values()), orphans


def test_find_orphans_detects_project_and_dataset_orphans():
    orphans = find_orphans(
        clients={1},
        domains={10},
        client_domains=[],
        projects=[(5, 99)],            # bad client
        project_domains=[(5, 88)],     # bad domain
        client_datasets=[(77, 10, "ds")],  # bad client
    )
    assert (5, 99) in orphans["projects_bad_client"]
    assert (5, 88) in orphans["project_domains_bad_domain"]
    assert (77, 10, "ds") in orphans["client_datasets_bad_client"]
