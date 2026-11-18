"""Tests for normalize_events.py, run on the sample items in fixtures/.

The sorter runs once on fixtures/ with a fixed date and a temporary output folder. Each
classification case below is its own test method, named after what the case guards, so a
failing rule shows up by name. Run: python3 -m unittest discover -s tests
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures")
SCRIPT = os.path.join(ROOT, "normalize_events.py")
TODAY = "2026-11-02"
CITIES = ["san-diego", "los-angeles", "orange-county", "sf-bay", "las-vegas"]

SUMMARY_LINE = (
    "san-diego:radar=26 los-angeles:radar=10 orange-county:radar=10 "
    "sf-bay:radar=22 las-vegas:radar=8 total_radar=76"
)


def run_normalize(workdir, out, today=TODAY):
    env = dict(os.environ, RADAR_OUT=out, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, SCRIPT, workdir, today], env=env,
                          capture_output=True, text=True, timeout=120)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class NormalizeBase(unittest.TestCase):
    """Runs the sorter once for the whole class and indexes the result."""

    out = None
    proc = None

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="radar-normalize-")
        cls.proc = run_normalize(FIXTURES, cls.out)
        if cls.proc.returncode != 0:
            raise RuntimeError("normalize_events.py failed: " + cls.proc.stderr[-400:])
        cls.feed = load(os.path.join(cls.out, "ALL-CITIES.json"))
        cls.city = {c: load(os.path.join(cls.out, c, TODAY + ".json")) for c in CITIES}
        cls.online = {e["id"]: e for e in cls.feed["online"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def event(self, city, event_id):
        for e in self.city[city]["events"]:
            if e["id"] == event_id:
                return e
        self.fail("%s not in %s: ids are %s" % (
            event_id, city, [e["id"] for e in self.city[city]["events"]][:8]))


# (test name, city, event id, expected fields). "online" entries are read from the
# per-city file as well, where they carry bucket "online".
CASES = [
    # San Diego, Meetup
    ("pickleball_is_not_tech", "san-diego", "meetup:440146824", {"bucket": "non_tech", "topic": "other"}),
    ("tide_pool_walk_is_not_tech", "san-diego", "meetup:478641093", {"bucket": "non_tech", "topic": "other"}),
    ("registry_group_keeps_hardware", "san-diego", "meetup:457519973", {"topic": "hardware", "group_registry": "Example Makers Guild"}),
    ("school_outreach_title_is_not_tech", "san-diego", "meetup:427820347", {"bucket": "non_tech", "topic": "other"}),
    ("online_security_event_is_cyber", "san-diego", "meetup:420921766", {"bucket": "online", "topic": "cyber", "online": True}),
    ("reinforcement_learning_is_ai", "san-diego", "meetup:436785732", {"bucket": "online", "topic": "ai"}),
    ("cuda_pytorch_is_ai", "san-diego", "meetup:426065580", {"bucket": "online", "topic": "ai"}),
    ("security_course_is_a_cyber_workshop", "san-diego", "meetup:468089579", {"topic": "cyber", "format": "workshop"}),
    ("manufacturing_course_is_hardware_workshop", "san-diego", "meetup:advanced-manufacturing", {"bucket": "radar", "topic": "hardware", "format": "workshop"}),
    ("certified_teams_only_title_is_noise", "san-diego", "meetup:certified-teams-noise", {"bucket": "non_tech", "topic": "other"}),
    ("space_yacht_night_is_noise", "san-diego", "meetup:space-yacht-night", {"bucket": "non_tech", "topic": "other"}),
    ("ruby_host_is_tech", "san-diego", "meetup:host-sd-ruby", {"bucket": "radar", "topic": "tech"}),
    ("in_person_vr_stays_in_person", "san-diego", "meetup:vr-in-person", {"bucket": "radar", "online": False, "topic": "hardware"}),
    ("codathon_title_is_a_hackathon", "san-diego", "meetup:codathon-title", {"bucket": "radar", "topic": "tech", "format": "hackathon"}),
    ("real_estate_developers_are_noise", "san-diego", "meetup:realestate-developers-noise", {"bucket": "non_tech", "topic": "other"}),
    ("developer_first_title_is_tech", "san-diego", "meetup:developer-first-devrel", {"bucket": "radar", "topic": "tech"}),
    ("swift_host_online_event_is_tech", "san-diego", "meetup:swift-pairing-host", {"bucket": "online", "topic": "tech"}),
    ("spanish_founder_description_is_not_tech", "san-diego", "meetup:spanish-founder-desc", {"bucket": "online", "topic": "other"}),
    ("informatics_group_host_is_tech", "san-diego", "meetup:sagim-guard", {"bucket": "radar", "topic": "tech"}),
    ("ieee_title_is_tech", "san-diego", "meetup:405580454", {"bucket": "radar", "topic": "tech"}),
    ("ux_title_social_is_tech_social", "san-diego", "meetup:ux-speakeasy-title", {"bucket": "radar", "topic": "tech", "format": "social"}),
    ("keyword_in_description_past_200_chars_is_noise", "san-diego", "meetup:keyword-past-200", {"bucket": "non_tech", "topic": "other"}),
    # San Diego, Eventbrite
    ("excluded_host_certification_is_training_mill", "san-diego", "eventbrite:excluded-host-ceh", {"bucket": "training_mill"}),
    ("excluded_host_convention_is_training_mill", "san-diego", "eventbrite:excluded-host-convention", {"bucket": "training_mill"}),
    ("certification_title_template_is_training_mill", "san-diego", "eventbrite:bootcamp-outlet-cert", {"bucket": "training_mill"}),
    # San Diego, Luma
    ("language_model_title_is_ai", "san-diego", "luma:evt-k3Qm7LpT2WxNa5c", {"bucket": "radar", "topic": "ai"}),
    ("sidecar_text_does_not_change_topic", "san-diego", "luma:sidecar-no-topic", {"topic": "ai"}),
    # Los Angeles
    ("socal_python_host_is_tech", "los-angeles", "meetup:host-socal-python", {"bucket": "radar", "topic": "tech"}),
    ("socalsec_host_is_cyber", "los-angeles", "meetup:host-socalsec", {"bucket": "radar", "topic": "cyber"}),
    ("cpp_virtual_is_online_tech", "los-angeles", "meetup:cpp-virtual", {"bucket": "online", "online": True, "topic": "tech"}),
    ("malware_title_is_cyber", "los-angeles", "meetup:malware-title", {"bucket": "radar", "topic": "cyber"}),
    ("coworking_title_is_a_social", "los-angeles", "meetup:coworking-social", {"bucket": "radar", "format": "social"}),
    ("threat_hunting_title_is_online_cyber", "los-angeles", "meetup:threat-hunting-title", {"bucket": "online", "topic": "cyber"}),
    ("tokenized_assets_title_is_online_tech", "los-angeles", "meetup:tokenized-assets-title", {"bucket": "online", "topic": "tech"}),
    ("wellness_shelf_is_noise", "los-angeles", "luma:wellness-shelf-noise", {"bucket": "non_tech", "topic": "other"}),
    ("game_developer_mixer_host_is_tech", "los-angeles", "luma:igda-mixer-host", {"bucket": "radar", "topic": "tech"}),
    ("lfdt_host_is_online_tech", "los-angeles", "meetup:rollups-lfdt-host", {"bucket": "online", "topic": "tech"}),
    ("graph_database_host_is_online_tech", "los-angeles", "meetup:graphcon-host-item", {"bucket": "online", "topic": "tech"}),
    ("founders_title_is_tech", "los-angeles", "meetup:founders-playbook-title", {"bucket": "radar", "topic": "tech"}),
    ("no_software_boilerplate_is_not_tech", "los-angeles", "meetup:no-software-boilerplate", {"bucket": "online", "topic": "other"}),
    ("ai_developer_group_host_is_ai", "los-angeles", "meetup:ai-dev-group-host", {"bucket": "online", "topic": "ai"}),
    ("circuits_social_title_is_hardware", "los-angeles", "meetup:circuits-social-title", {"bucket": "radar", "topic": "hardware"}),
    ("wearables_title_is_hardware", "los-angeles", "meetup:wearables-title", {"bucket": "radar", "topic": "hardware"}),
    ("glued_tech_week_title_is_tech", "los-angeles", "meetup:latechweek-glued", {"bucket": "radar", "topic": "tech"}),
    ("isc2_title_is_cyber", "los-angeles", "luma:isc2-la-title", {"bucket": "radar", "topic": "cyber"}),
    ("skier_snowboarder_happy_hour_is_noise", "los-angeles", "meetup:snow-social-noise", {"bucket": "non_tech", "topic": "other"}),
    ("r_users_host_is_online_tech", "los-angeles", "meetup:socal-r-host", {"bucket": "online", "topic": "tech"}),
    # Orange County
    ("tai_chi_is_not_ai", "orange-county", "meetup:tai-chi-boundary", {"bucket": "non_tech", "topic": "other"}),
    ("quoted_ai_is_ai", "orange-county", "meetup:quoted-ai-boundary", {"bucket": "radar", "topic": "ai"}),
    ("cnc_course_is_hardware_workshop", "orange-county", "meetup:cnc-course-format", {"bucket": "radar", "topic": "hardware", "format": "workshop"}),
    ("red_hat_host_is_tech", "orange-county", "meetup:host-rhug", {"bucket": "radar", "topic": "tech"}),
    ("meet_link_in_description_is_online", "orange-county", "meetup:ocai-remote-miss", {"bucket": "online", "online": True, "topic": "ai"}),
    ("indie_developers_host_is_tech", "orange-county", "meetup:oc-indies-host", {"bucket": "radar", "topic": "tech"}),
    ("fake_vendor_meetup_is_training_mill", "orange-county", "meetup:fake-claude-meetup", {"bucket": "training_mill"}),
    ("underground_host_is_cyber", "orange-county", "meetup:irvine-underground-host", {"bucket": "radar", "topic": "cyber"}),
    ("printer_host_is_hardware", "orange-county", "meetup:threedpoc-host", {"bucket": "radar", "topic": "hardware"}),
    ("chatgpt_description_is_ai", "orange-county", "meetup:417775709", {"bucket": "radar", "topic": "ai"}),
    ("tech_week_title_is_tech", "orange-county", "meetup:401711464", {"bucket": "radar", "topic": "tech"}),
    ("grace_hopper_title_is_tech", "orange-county", "meetup:492828605", {"bucket": "radar", "topic": "tech"}),
    ("figma_title_is_tech", "orange-county", "meetup:figma-ux-title", {"bucket": "radar", "topic": "tech"}),
    # San Francisco Bay
    ("jigsaw_maker_night_is_noise", "sf-bay", "meetup:maker-jigsaw-noise", {"bucket": "non_tech", "topic": "other"}),
    ("maker_tour_signal_is_hardware", "sf-bay", "meetup:maker-tour-signal", {"bucket": "radar", "topic": "hardware"}),
    ("electronics_title_is_hardware", "sf-bay", "meetup:electronics-title", {"bucket": "radar", "topic": "hardware"}),
    ("programming_languages_host_is_tech", "sf-bay", "meetup:host-programming-languages", {"bucket": "radar", "topic": "tech"}),
    ("startup_island_host_is_tech", "sf-bay", "meetup:host-startup-island", {"bucket": "radar", "topic": "tech"}),
    ("laser_jigsaw_is_hardware", "sf-bay", "meetup:laser-jigsaw-hardware", {"bucket": "radar", "topic": "hardware"}),
    ("pyladies_title_is_tech", "sf-bay", "meetup:pyladies-title", {"bucket": "radar", "topic": "tech"}),
    ("laser_lab_title_is_hardware", "sf-bay", "meetup:laser-lab-title", {"bucket": "radar", "topic": "hardware"}),
    ("dot_ai_domain_is_not_ai", "sf-bay", "meetup:dot-ai-domain", {"bucket": "radar", "topic": "tech"}),
    ("verilog_title_is_tech", "sf-bay", "luma:verilog-title", {"bucket": "radar", "topic": "tech"}),
    ("formal_math_title_is_tech", "sf-bay", "luma:formal-math-title", {"bucket": "radar", "topic": "tech"}),
    ("uppercase_ros_title_is_ai", "sf-bay", "luma:ros-title", {"bucket": "radar", "topic": "ai"}),
    ("openmetadata_title_is_online_tech", "sf-bay", "meetup:openmetadata-title", {"bucket": "online", "topic": "tech"}),
    ("secret_scanning_title_is_online_cyber", "sf-bay", "meetup:secret-scanning-title", {"bucket": "online", "topic": "cyber"}),
    ("plural_llms_description_is_ai", "sf-bay", "meetup:llms-plural-desc", {"bucket": "radar", "topic": "ai"}),
    ("speculative_decoding_host_is_online_ai", "sf-bay", "meetup:spec-decoding-host", {"bucket": "online", "topic": "ai"}),
    ("transformer_paper_host_is_online_ai", "sf-bay", "meetup:transformer-paper-host", {"bucket": "online", "topic": "ai"}),
    ("identity_attack_surface_is_online_cyber", "sf-bay", "meetup:identity-attack-surface", {"bucket": "online", "topic": "cyber"}),
    ("solder_kit_class_is_hardware", "sf-bay", "meetup:solder-kit-class", {"bucket": "radar", "topic": "hardware"}),
    ("woodworking_title_is_hardware", "sf-bay", "meetup:woodworking-title", {"bucket": "radar", "topic": "hardware"}),
    ("melee_game_night_is_noise", "sf-bay", "meetup:melee-game-night", {"bucket": "non_tech", "topic": "other"}),
    ("mcp_title_is_online_ai", "sf-bay", "meetup:mcp-online-title", {"bucket": "online", "topic": "ai"}),
    ("rocq_title_is_tech", "sf-bay", "meetup:rocq-title", {"bucket": "radar", "topic": "tech"}),
    ("codex_build_night_is_tech", "sf-bay", "meetup:codex-build-night", {"bucket": "radar", "topic": "tech"}),
    ("laser_basics_title_is_hardware", "sf-bay", "meetup:laser-basics-title", {"bucket": "radar", "topic": "hardware"}),
    ("hugging_face_description_is_online_ai", "sf-bay", "meetup:hugging-face-desc", {"bucket": "online", "topic": "ai"}),
    ("hardware_store_hike_is_noise", "sf-bay", "meetup:454513097", {"bucket": "non_tech", "topic": "other"}),
    ("metal_lathe_is_a_hardware_workshop", "sf-bay", "meetup:484707102", {"bucket": "radar", "topic": "hardware", "format": "workshop"}),
    ("game_development_is_tech", "sf-bay", "meetup:401308717", {"bucket": "radar", "topic": "tech"}),
    ("maker_social_is_hardware", "sf-bay", "meetup:420114078", {"bucket": "radar", "topic": "hardware"}),
    ("vr_theater_title_is_hardware", "sf-bay", "meetup:vr-theater-title", {"bucket": "radar", "topic": "hardware"}),
    ("bootcamp_outlet_host_is_training_mill", "sf-bay", "luma:bootcamp-outlet-cert", {"bucket": "training_mill"}),
    # Las Vegas
    ("bitcoin_title_is_tech", "las-vegas", "meetup:bitcoin-title", {"bucket": "radar", "topic": "tech"}),
    ("founder_title_is_tech", "las-vegas", "meetup:foundernv-title", {"bucket": "radar", "topic": "tech"}),
    ("wordpress_title_is_tech", "las-vegas", "meetup:wordpress-title", {"bucket": "radar", "topic": "tech"}),
    ("swiftui_title_is_tech", "las-vegas", "meetup:swiftui-title", {"bucket": "radar", "topic": "tech"}),
    ("okta_title_is_tech", "las-vegas", "meetup:okta-title", {"bucket": "radar", "topic": "tech"}),
    ("claude_talk_keeps_talk_format", "las-vegas", "meetup:claude-cowork-talk", {"bucket": "radar", "format": "talk"}),
    ("hackerspace_stream_host_is_online_hardware", "las-vegas", "meetup:synshop-stream-host", {"bucket": "online", "topic": "hardware"}),
    ("inner_engineering_is_noise", "las-vegas", "meetup:478826666", {"bucket": "non_tech", "topic": "other"}),
    ("fintech_conference_title_is_tech", "las-vegas", "luma:evt-money-satellite", {"bucket": "radar", "topic": "tech"}),
    ("digital_health_title_is_tech", "las-vegas", "luma:digital-health-title", {"bucket": "radar", "topic": "tech"}),
    # Luma host, time and sidecar rules
    ("luma_personal_organizer_with_hosts", "san-diego", "luma:personal-hosts", {"host": "Makers & Backers, Host A"}),
    ("luma_personal_organizer_without_hosts", "san-diego", "luma:personal-no-hosts", {"host": None}),
    ("luma_real_organizer_wins", "san-diego", "luma:real-organizer", {"host": "Harbor Robotics Lab"}),
    ("luma_multiday_end_keeps_end_date", "san-diego", "luma:multiday-end", {"start": "2026-11-09T09:00", "end": "2026-11-13T17:00"}),
    ("luma_end_before_start_is_dropped", "san-diego", "luma:end-before-start", {"end": None}),
    ("eventbrite_end_from_end_fields", "san-diego", "eventbrite:end-fields", {"end": "2026-11-11T20:30"}),
    ("eventbrite_hidden_end_is_dropped", "san-diego", "eventbrite:hidden-end", {"end": None}),
    ("eventbrite_end_without_time_is_dropped", "san-diego", "eventbrite:end-without-time", {"end": None}),
]


def _make_case(city, event_id, expected):
    def test(self):
        e = self.event(city, event_id)
        for key, want in expected.items():
            self.assertEqual(e.get(key), want, "%s.%s" % (event_id, key))
    return test


class ClassificationCases(NormalizeBase):
    """One generated test per regression case in CASES."""


for _name, _city, _id, _expected in CASES:
    setattr(ClassificationCases, "test_" + _name, _make_case(_city, _id, _expected))


class SummaryAndFiles(NormalizeBase):
    def test_summary_line(self):
        self.assertEqual(self.proc.stdout.splitlines()[0], SUMMARY_LINE)

    def test_files_written(self):
        written = []
        for base, _dirs, names in os.walk(self.out):
            for n in names:
                written.append(os.path.relpath(os.path.join(base, n), self.out))
        expected = ["ALL-CITIES.json", "LATEST.json", "LATEST.md", "_feed/%s.json" % TODAY]
        for c in CITIES:
            expected += ["%s/%s" % (c, n) for n in
                         ("LATEST.json", "LATEST.md", TODAY + ".json", TODAY + ".md")]
        self.assertEqual(sorted(written), sorted(expected))

    def test_no_temporary_files_left_behind(self):
        for _base, _dirs, names in os.walk(self.out):
            for n in names:
                self.assertFalse(n.endswith(".tmp") or n.startswith("."), n)

    def test_latest_page_lists_the_cities_and_the_filtered_section(self):
        with open(os.path.join(self.out, "LATEST.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Example Makers Guild", text)
        self.assertTrue(len(text.splitlines()) > 20)

    def test_index_table_rows_carry_the_counts_of_the_feed(self):
        # source: mutation run, the Raw column once printed the kept count
        with open(os.path.join(self.out, "LATEST.md"), encoding="utf-8") as fh:
            rows = [ln for ln in fh.read().splitlines() if ln.startswith("| ") and "/LATEST.md](" in ln]
        self.assertEqual(len(rows), len(self.feed["cities"]))
        for row, city in zip(rows, self.feed["cities"]):
            cells = [c.strip() for c in row.strip("|").split("|")]
            counts = city["counts"]
            self.assertEqual(cells[:4], [city["label"], str(counts["radar"]), str(counts["online"]),
                                         str(counts["raw"])])
        self.assertTrue(any(c["counts"]["raw"] != c["counts"]["kept"] for c in self.feed["cities"]))

    def test_latest_is_a_copy_of_the_dated_feed(self):
        self.assertEqual(load(os.path.join(self.out, "LATEST.json")), self.feed)
        self.assertEqual(load(os.path.join(self.out, "_feed", TODAY + ".json")), self.feed)


class FeedContract(NormalizeBase):
    EVENT_KEYS = {"id", "source", "title", "url", "start", "online", "venue", "city", "host",
                  "free", "topic", "desc", "summary", "end", "format", "bucket"}

    def test_top_level_keys(self):
        self.assertEqual(self.feed["schema_version"], 4)
        for key in ("generated_at", "timezone_note", "topics", "formats", "partial",
                    "partial_legs", "cities", "online"):
            self.assertIn(key, self.feed)
        self.assertIs(self.feed["partial"], False)
        self.assertEqual(self.feed["partial_legs"], [])

    def test_at_least_twenty_radar_events(self):
        total = sum(len(c["events"]) for c in self.feed["cities"])
        self.assertGreaterEqual(total, 20)
        self.assertEqual(total, 76)

    def test_every_active_city_is_present_with_matching_counts(self):
        slugs = sorted(c["slug"] for c in self.feed["cities"] if c["status"] == "active")
        with open(os.path.join(ROOT, "cities.json"), encoding="utf-8") as fh:
            expected = sorted(c["slug"] for c in json.load(fh)["active"])
        self.assertEqual(slugs, expected)
        for c in self.feed["cities"]:
            if c["status"] == "active":
                self.assertEqual(c["counts"]["radar"], len(c["events"]))
                for key in ("slug", "label", "status", "tz", "counts", "events", "pulled_at"):
                    self.assertIn(key, c)

    def test_radar_events_carry_the_full_key_set(self):
        for c in self.feed["cities"]:
            for e in c["events"]:
                self.assertTrue(self.EVENT_KEYS <= set(e), e["id"])
                self.assertEqual(e["bucket"], "radar")
                self.assertIsInstance(e["online"], bool)
                self.assertIn(e["topic"], self.feed["topics"])
                self.assertIn(e["format"], self.feed["formats"])

    def test_online_events_carry_their_cities(self):
        self.assertTrue(self.feed["online"])
        for e in self.feed["online"]:
            self.assertTrue(self.EVENT_KEYS <= set(e), e["id"])
            self.assertIs(e["online"], True)
            self.assertEqual(e["bucket"], "online")
            self.assertTrue(e["cities"])

    def test_feed_has_no_event_id_twice_inside_a_city(self):
        for c in CITIES:
            ids = [e["id"] for e in self.city[c]["events"]]
            self.assertEqual(len(ids), len(set(ids)), c)

    def test_start_strings_are_city_wall_time(self):
        for c in self.feed["cities"]:
            for e in c["events"]:
                self.assertRegex(e["start"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")


class RulesAcrossSources(NormalizeBase):
    def test_excluded_hosts_are_dropped_from_the_feed_and_counted(self):
        sd = self.city["san-diego"]
        self.assertEqual(sd["counts"]["training_mill"], 3)
        feed_ids = {e["id"] for e in self.feed["cities"][0]["events"]}
        self.assertEqual(self.feed["cities"][0]["slug"], "san-diego")
        for mill in ("eventbrite:excluded-host-ceh", "eventbrite:excluded-host-convention",
                     "eventbrite:bootcamp-outlet-cert"):
            self.assertNotIn(mill, feed_ids)
        self.assertEqual(self.event("san-diego", "eventbrite:excluded-host-ceh")["host"],
                         "acme certs academy")

    def test_registry_group_is_marked_and_counted(self):
        e = self.event("san-diego", "meetup:457519973")
        self.assertEqual(e["group_registry"], "Example Makers Guild")
        self.assertEqual(self.city["san-diego"]["counts"]["registry_groups_hit"], 1)
        others = [x for x in self.city["san-diego"]["events"]
                  if x["id"] != "meetup:457519973" and "group_registry" in x]
        self.assertEqual(others, [])

    def test_same_event_from_three_sources_appears_once_in_source_order(self):
        sd = self.city["san-diego"]["events"]
        merged = [e for e in sd if e["title"] == "AI Builders Roundtable Evening"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "luma")
        self.assertEqual(sorted(merged[0]["also"]), [
            "https://www.eventbrite.com/e/ai-builders-roundtable",
            "https://www.meetup.com/builders-roundtable/events/400000001/",
        ])
        self.assertEqual(self.city["san-diego"]["counts"]["deduped"], 2)

    def test_same_event_from_two_meetup_listings_appears_once(self):
        sf = [e for e in self.city["sf-bay"]["events"] if "Founders" in e["title"]
              and e["host"] == "Builders and Founders Circle"]
        self.assertEqual(len(sf), 1)
        self.assertEqual(len(sf[0]["also"]), 1)
        self.assertEqual(self.city["sf-bay"]["counts"]["deduped"], 1)

    def test_online_event_listed_in_five_cities_is_merged_once(self):
        title = "(OT)\u00b2Sec Online\u2122: Practical Plant-Floor Skills for Cybersecurity Teams"
        hits = [e for e in self.feed["online"] if e["title"] == title]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["cities"], sorted(CITIES))
        self.assertEqual(len(hits[0]["also"]), 3)

    def test_eventbrite_item_outside_the_region_is_dropped_and_counted(self):
        sd = self.city["san-diego"]
        self.assertEqual(sd["counts"]["dropped_out_of_region"], 1)
        with open(os.path.join(FIXTURES, "items-san-diego-eventbrite-sample.json"),
                  encoding="utf-8") as fh:
            items = json.load(fh)
        outside = [i for i in items if "Fresno" in json.dumps(i)]
        self.assertEqual(len(outside), 1)
        ids = {e["id"] for e in sd["events"]}
        self.assertFalse([i for i in ids if "out-of-region" in i])

    def test_online_event_is_not_counted_as_in_person(self):
        sd = self.city["san-diego"]
        self.assertEqual(sd["counts"]["online"], 5)
        self.assertEqual(len([e for e in sd["events"] if e["bucket"] == "online"]), 5)

    def test_bucket_counts_add_up_to_kept_events(self):
        for c in CITIES:
            n = self.city[c]["counts"]
            self.assertEqual(n["kept"], n["radar"] + n["training_mill"] + n["non_tech"] + n["online"], c)
            self.assertEqual(n["kept"], len(self.city[c]["events"]), c)

    def test_summary_is_a_cut_with_ellipsis_and_no_markdown(self):
        e = self.event("san-diego", "meetup:summary-wordcut")
        self.assertTrue(e["summary"].endswith("\u2026"))
        self.assertLessEqual(len(e["summary"]), 601)
        self.assertIn("Sample Link", e["summary"])
        self.assertNotIn("](", e["summary"])
        self.assertNotIn("**", e["summary"])
        self.assertNotIn("Zanzib", e["summary"])
        self.assertEqual(len(e["desc"]), 200)
        self.assertEqual(e["end"], "2026-11-21T20:15")

    def test_luma_sidecar_text_fills_a_blank_description(self):
        e = self.event("san-diego", "luma:sidecar-summary")
        self.assertTrue(e["summary"].startswith("Doors open with a quick tour"))
        self.assertTrue(e["summary"].endswith("\u2026"))
        self.assertLessEqual(len(e["summary"]), 601)
        self.assertEqual(e["desc"], "")

    def test_luma_raw_description_wins_over_sidecar_text(self):
        e = self.event("san-diego", "luma:raw-description-wins")
        self.assertTrue(e["summary"].startswith("The agenda text"))
        self.assertNotIn("different blurb", e["summary"])

    def test_luma_title_echo_line_is_removed_from_the_summary(self):
        e = self.event("san-diego", "luma:title-echo")
        self.assertTrue(e["summary"].startswith("People swap notes"))
        self.assertFalse(e["summary"].lower().startswith("quiet signal"))

    def test_luma_list_id_becomes_a_string(self):
        hits = [e for e in self.city["san-diego"]["events"] if e["title"] == "AI Array Id Night"]
        self.assertEqual(len(hits), 1)
        self.assertIsInstance(hits[0]["id"], str)


class AtomicWrite(unittest.TestCase):
    """write_atomic must replace the target in one rename, never write into it directly."""

    SCRIPT = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, %(root)r)
sys.argv = ["normalize_events.py", %(fixtures)r, %(today)r]
os.environ["RADAR_OUT"] = %(out)r
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    import normalize_events as n
target = Path(%(target)r)
target.write_text("old")
real = os.replace
calls = []
def failing(a, b):
    calls.append((str(a), str(b)))
    raise OSError("rename refused")
os.replace = failing
try:
    n.write_atomic(target, "new")
    raised = False
except OSError:
    raised = True
os.replace = real
print(json.dumps({"raised": raised, "calls": calls, "content": target.read_text()}))
"""

    def test_target_keeps_old_content_when_the_rename_fails(self):
        out = tempfile.mkdtemp(prefix="radar-atomic-")
        try:
            target = os.path.join(out, "x.json")
            code = self.SCRIPT % {"root": ROOT, "fixtures": FIXTURES, "today": TODAY,
                                  "out": os.path.join(out, "o"), "target": target}
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                  timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(result["raised"])
            self.assertEqual(result["calls"][0], [target + ".tmp", target])
            self.assertEqual(result["content"], "old")
        finally:
            shutil.rmtree(out, ignore_errors=True)


class PastEvents(unittest.TestCase):
    def test_events_before_the_run_date_are_dropped_and_counted(self):
        out = tempfile.mkdtemp(prefix="radar-past-")
        try:
            proc = run_normalize(FIXTURES, out, "2026-11-12")
            self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
            sd = load(os.path.join(out, "san-diego", "2026-11-12.json"))
            self.assertGreater(sd["counts"]["dropped_past"], 0)
            for e in sd["events"]:
                self.assertGreaterEqual(e["start"][:10], "2026-11-12")
        finally:
            shutil.rmtree(out, ignore_errors=True)


class PartialLegs(unittest.TestCase):
    def test_partial_leg_is_stamped_into_the_payloads(self):
        work = tempfile.mkdtemp(prefix="radar-partial-work-")
        out = tempfile.mkdtemp(prefix="radar-partial-out-")
        try:
            for name in os.listdir(FIXTURES):
                shutil.copy(os.path.join(FIXTURES, name), os.path.join(work, name))
            with open(os.path.join(work, "partial-legs.txt"), "w", encoding="utf-8") as fh:
                fh.write("san-diego-luma\n")
            proc = run_normalize(work, out)
            self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
            feed = load(os.path.join(out, "ALL-CITIES.json"))
            self.assertIs(feed["partial"], True)
            self.assertEqual(feed["partial_legs"], ["san-diego-luma"])
            sd = load(os.path.join(out, "san-diego", TODAY + ".json"))
            self.assertIs(sd["partial"], True)
            self.assertEqual(sd["partial_legs"], ["san-diego-luma"])
            la = load(os.path.join(out, "los-angeles", TODAY + ".json"))
            self.assertIs(la["partial"], False)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(out, ignore_errors=True)

    def test_a_folder_without_items_reports_zero_events(self):
        # This test was changed on purpose: a folder with no items file at all
        # now exits 1 and writes nothing (see ZeroUsableItems). The same zero counts still
        # hold for items files that hold empty lists, which is what this test now sets up.
        work = tempfile.mkdtemp(prefix="radar-empty-work-")
        out = tempfile.mkdtemp(prefix="radar-empty-out-")
        try:
            with open(os.path.join(work, "items-san-diego-luma.json"), "w", encoding="utf-8") as fh:
                fh.write("[]\n")
            proc = run_normalize(work, out)
            self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
            self.assertEqual(proc.stdout.splitlines()[0],
                             "san-diego:radar=0 los-angeles:radar=0 orange-county:radar=0 "
                             "sf-bay:radar=0 las-vegas:radar=0 total_radar=0")
            feed = load(os.path.join(out, "ALL-CITIES.json"))
            self.assertEqual(sum(len(c["events"]) for c in feed["cities"]), 0)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(out, ignore_errors=True)


def _tree(cities=None, groups=None):
    """A temporary copy of the script with its own config files; returns (folder, script)."""
    folder = tempfile.mkdtemp(prefix="radar-tree-")
    shutil.copy(SCRIPT, os.path.join(folder, "normalize_events.py"))
    for name, doc in (("cities.json", cities), ("groups.json", groups)):
        target = os.path.join(folder, name)
        if doc is None:
            shutil.copy(os.path.join(ROOT, name), target)
        elif isinstance(doc, str):
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(doc)
        else:
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
    return folder, os.path.join(folder, "normalize_events.py")


def _run(script, args, out, cwd=None):
    env = dict(os.environ, RADAR_OUT=out, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, script] + list(args), env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=120)


def _files_under(folder):
    found = []
    for base, _dirs, names in os.walk(folder):
        found += [os.path.join(base, n) for n in names]
    return found


def _city(**changes):
    city = {"slug": "solo", "label": "Solo", "tz": "America/Los_Angeles", "luma_city": None,
            "meetup": {"city": "Solo", "state": "CA", "radius": 10}, "kws": ["AI"]}
    city.update(changes)
    return city


class ZeroUsableItems(unittest.TestCase):
    """A folder with no usable items file writes nothing and exits 1."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="radar-zero-work-")
        self.out = os.path.join(tempfile.mkdtemp(prefix="radar-zero-parent-"), "out")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.out), ignore_errors=True)

    def _expect_refusal(self, proc):
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("ERROR: zero usable items files in %s, nothing was written" % self.work,
                      proc.stderr.splitlines())
        self.assertEqual(proc.stdout, "")
        self.assertFalse(os.path.exists(self.out), "the output folder was created")

    def test_an_empty_folder_exits_1_and_writes_nothing(self):
        # source: a night with no data replaced the feed with an empty one
        self._expect_refusal(run_normalize(self.work, self.out))

    def test_files_that_are_not_lists_do_not_count(self):
        # source: an error object from an actor is not data
        for name, text in (("items-san-diego-luma.json", '{"error": "x"}'),
                           ("items-san-diego-meetup-AI.json", "not json"),
                           ("items-la-meetup-AI.json", "null")):
            with open(os.path.join(self.work, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        proc = run_normalize(self.work, self.out)
        self._expect_refusal(proc)
        self.assertIn("WARN: items-san-diego-luma.json: not a JSON list, skipped", proc.stderr)

    def test_an_empty_list_is_usable_and_a_feed_is_written(self):
        # source: a quiet week is data, only a missing week is not
        with open(os.path.join(self.work, "items-san-diego-meetup-AI.json"), "w", encoding="utf-8") as fh:
            fh.write("[]")
        proc = run_normalize(self.work, self.out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "ALL-CITIES.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "LATEST.md")))

    def test_an_existing_feed_is_not_touched_by_a_refused_run(self):
        # source: the old feed must stay byte for byte
        os.makedirs(self.out)
        with open(os.path.join(self.out, "ALL-CITIES.json"), "w", encoding="utf-8") as fh:
            fh.write('{"old": true}\n')
        proc = run_normalize(self.work, self.out)
        self.assertEqual(proc.returncode, 1)
        with open(os.path.join(self.out, "ALL-CITIES.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"old": true}\n')
        self.assertEqual(os.listdir(self.out), ["ALL-CITIES.json"])


class ConfigCheck(unittest.TestCase):
    """`--check` names every problem in the configuration files."""

    def tearDown(self):
        for path in getattr(self, "made", []):
            shutil.rmtree(path, ignore_errors=True)

    def tree(self, cities=None, groups=None):
        folder, script = _tree(cities, groups)
        self.made = getattr(self, "made", []) + [folder]
        return folder, script

    def check(self, cities=None, groups=None, date=None):
        folder, script = self.tree(cities, groups)
        out = os.path.join(folder, "out")
        proc = _run(script, ["--check"] + ([date] if date else []), out, cwd=folder)
        self.assertFalse(os.path.exists(out), "check mode wrote the output folder")
        return proc

    def test_the_shipped_configuration_passes_and_writes_nothing(self):
        # source: the check must accept what ships
        proc = self.check(date="2026-11-02")
        self.assertEqual((proc.returncode, proc.stdout.strip(), proc.stderr), (0, "config ok", ""))

    def test_dates_that_are_not_calendar_dates_are_refused(self):
        # source: a bad RADAR_TODAY must stop before any paid request
        for value in ("2026-02-30", "2026-13-01", "26-11-02", "2026-1-2", "tomorrow", "2026-11-02\n"):
            with self.subTest(value=value):
                proc = self.check(date=value)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stderr.splitlines(),
                                 ["date: %s is not a calendar date in YYYY-MM-DD form" % value]
                                 if "\n" not in value else proc.stderr.splitlines())
                self.assertIn("is not a calendar date in YYYY-MM-DD form", proc.stderr)

    def test_a_leap_day_is_a_calendar_date(self):
        # source: a real date must pass
        self.assertEqual(self.check(date="2028-02-29").returncode, 0)

    def test_every_cities_rule_has_its_own_problem_line(self):
        # source: one problem line per rule, in the form "<file>: <problem>"
        good = _city()

        def with_city(**changes):
            return {"active": [_city(**changes)]}

        def without(key):
            city = _city()
            del city[key]
            return {"active": [city]}

        cases = [
            ("top-level list", [good], "the top-level value must be a JSON object"),
            ("no active", {}, "active must be a list with at least one city"),
            ("empty active", {"active": []}, "active must be a list with at least one city"),
            ("entry not object", {"active": ["x"]}, "active[0] must be an object"),
            ("slug upper case", with_city(slug="Solo"), "slug 'Solo' must be lower-case"),
            ("slug with space", with_city(slug="so lo"), "slug 'so lo' must be lower-case"),
            ("slug trailing newline", with_city(slug="solo\n"), "must be lower-case"),
            ("slug missing", without("slug"), "slug None must be lower-case"),
            ("label empty", with_city(label=""), "label must be a string"),
            ("label number", with_city(label=5), "label must be a string"),
            ("tz missing", without("tz"), "tz must be a time zone name"),
            ("tz unknown", with_city(tz="Mars/Base"), "is not a time zone this system knows"),
            ("luma_city number", with_city(luma_city=5), "luma_city must be a string or null"),
            ("meetup string", with_city(meetup="x"), "meetup must be an object"),
            ("meetup city empty", with_city(meetup={"city": "", "state": "CA", "radius": 5}),
             "meetup.city must be a string"),
            ("meetup state missing", with_city(meetup={"city": "A", "radius": 5}),
             "meetup.state must be a string"),
            ("radius zero", with_city(meetup={"city": "A", "state": "CA", "radius": 0}),
             "meetup.radius 0 must be a number above 0"),
            ("radius boolean", with_city(meetup={"city": "A", "state": "CA", "radius": True}),
             "meetup.radius True must be a number above 0"),
            ("radius text", with_city(meetup={"city": "A", "state": "CA", "radius": "5"}),
             "meetup.radius '5' must be a number above 0"),
            ("kws string", with_city(kws="AI"), "kws must be a list of strings"),
            ("kws number", with_city(kws=[5]), "kws entry 5 must be 1 to 40"),
            ("kws slash", with_city(kws=["a/b"]), "kws entry 'a/b' must be 1 to 40"),
            ("kws leading space", with_city(kws=[" AI"]), "must be 1 to 40"),
            ("kws too long", with_city(kws=["a" * 41]), "must be 1 to 40"),
            ("kws repeat", with_city(kws=["AI", "ai"]), "repeats another keyword"),
            ("registry text", with_city(registry="yes"), "registry must be true or false"),
            ("eventbrite string", with_city(eventbrite="x"), "eventbrite must be an object"),
            ("eventbrite region", with_city(eventbrite={"city": "A", "region": "x"}),
             "eventbrite.region must be a list of strings"),
            ("duplicate slug", {"active": [_city(), _city()]}, "repeats active[0].slug"),
            ("slug prefix", {"active": [_city(slug="san"), _city(slug="san-diego")]},
             "is the start of active[1].slug"),
        ]
        for label, doc, needle in cases:
            with self.subTest(case=label):
                proc = self.check(cities=doc)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(proc.stdout, "")
                lines = proc.stderr.splitlines()
                self.assertTrue(lines and all(l.startswith("cities.json: ") for l in lines), lines)
                self.assertTrue(any(needle in l for l in lines), (needle, lines))

    def test_cities_options_that_are_allowed_pass(self):
        # source: optional keys must stay optional
        doc = {"active": [
            _city(slug="a", luma_city="Alpha", registry=True, eventbrite={"city": "A", "region": ["X"]}),
            _city(slug="b", eventbrite={}),
            {k: v for k, v in _city(slug="c").items() if k != "luma_city"},
        ]}
        proc = self.check(cities=doc)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "config ok"), proc.stderr)

    def test_a_cities_file_that_cannot_be_parsed_is_one_problem_line(self):
        # source: a half-saved file must not give a traceback
        proc = self.check(cities="{not json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(len(proc.stderr.splitlines()), 1, proc.stderr)
        self.assertIn("cities.json: is not valid JSON", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_a_missing_cities_file_is_a_problem_line(self):
        # source: an unreadable file is a problem, not a crash
        folder, script = self.tree()
        os.remove(os.path.join(folder, "cities.json"))
        proc = _run(script, ["--check"], os.path.join(folder, "out"), cwd=folder)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cities.json: cannot be read", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_every_groups_rule_has_its_own_problem_line(self):
        # source: groups.json is hand edited
        cases = [
            ("top-level list", [], "the top-level value must be a JSON object"),
            ("groups text", {"groups": "x"}, "groups must be a list"),
            ("host_topics object", {"host_topics": {}}, "host_topics must be a list"),
            ("exclude_hosts number", {"exclude_hosts": 5}, "exclude_hosts must be a list"),
            ("group not object", {"groups": ["x"]}, "groups[0] must be an object"),
            ("group without match", {"groups": [{"name": "a", "category": "b"}]},
             "groups[0] must be an object with the text fields name, category, match"),
            ("host_topic without topic", {"host_topics": [{"match": "x"}]},
             "host_topics[0] must be an object"),
            ("exclude entry number", {"exclude_hosts": [5]}, "exclude_hosts[0] must be an object"),
        ]
        for label, doc, needle in cases:
            with self.subTest(case=label):
                proc = self.check(groups=doc)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                lines = proc.stderr.splitlines()
                self.assertTrue(lines and all(l.startswith("groups.json: ") for l in lines), lines)
                self.assertTrue(any(needle in l for l in lines), (needle, lines))

    def test_absent_groups_lists_count_as_empty(self):
        # source: an absent list is an empty list
        proc = self.check(groups={})
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "config ok"), proc.stderr)

    def test_problems_from_both_files_and_the_date_are_all_reported(self):
        # source: one run shows every problem
        proc = self.check(cities={"active": []}, groups=[], date="2026-02-30")
        self.assertEqual(proc.returncode, 2)
        prefixes = sorted(l.split(":")[0] for l in proc.stderr.splitlines())
        self.assertEqual(prefixes, ["cities.json", "date", "groups.json"])

    def test_a_pattern_that_does_not_compile_keeps_the_old_behaviour(self):
        # source: a bad regex is a WARN and the entry is skipped
        groups = {"groups": [{"name": "Bad", "category": "ai", "match": "(", "cadence": "x"}],
                  "host_topics": [{"topic": "ai", "match": "("}], "exclude_hosts": ["("]}
        check = self.check(groups=groups)
        self.assertEqual((check.returncode, check.stdout.strip()), (0, "config ok"), check.stderr)
        folder, script = self.tree(groups=groups)
        out = os.path.join(folder, "out")
        proc = _run(script, [FIXTURES, TODAY], out, cwd=folder)
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        self.assertEqual(sum("invalid" in l and l.startswith("WARN: groups.json") for l in proc.stderr.splitlines()), 3)
        self.assertTrue(os.path.isfile(os.path.join(out, "ALL-CITIES.json")))

    def test_a_group_without_cadence_venue_or_platform_still_prints(self):
        # source: optional text keys of a group must not end the run
        groups = {"groups": [{"name": "Plain Group", "category": "ai", "match": "plain group"}]}
        cities = {"active": [_city(slug="san-diego", registry=True)]}
        folder, script = self.tree(cities=cities, groups=groups)
        out = os.path.join(folder, "out")
        proc = _run(script, [FIXTURES, TODAY], out, cwd=folder)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        with open(os.path.join(out, "san-diego", "LATEST.md"), encoding="utf-8") as fh:
            self.assertIn("**Plain Group** (ai): ? @ ?, via ?", fh.read())

    def test_an_ordinary_run_checks_the_configuration_first(self):
        # source: a bad file must end the run before anything is read or written
        folder, script = self.tree(cities={"active": []})
        out = os.path.join(folder, "out")
        proc = _run(script, [FIXTURES, TODAY], out, cwd=folder)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("cities.json: active must be a list", proc.stderr)
        self.assertFalse(os.path.exists(out))

    def test_the_check_reads_no_work_folder(self):
        # source: check mode is for a night where the folder does not exist yet
        folder, script = self.tree()
        proc = _run(script, ["--check", TODAY, "extra"], os.path.join(folder, "out"), cwd=folder)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr.strip(), "usage: normalize_events.py WORK_DIR [YYYY-MM-DD]")


class CommandLine(unittest.TestCase):
    """A wrong command line, a path that is not a folder or a bad date exits 2 and writes nothing."""

    def setUp(self):
        self.parent = tempfile.mkdtemp(prefix="radar-cli-")
        self.out = os.path.join(self.parent, "out")
        self.work = os.path.join(self.parent, "work")
        os.makedirs(self.work)
        with open(os.path.join(self.work, "items-san-diego-meetup-AI.json"), "w", encoding="utf-8") as fh:
            fh.write("[]")

    def tearDown(self):
        shutil.rmtree(self.parent, ignore_errors=True)

    def refused(self, args, line):
        proc = _run(SCRIPT, args, self.out)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr.splitlines(), [line])
        self.assertFalse(os.path.exists(self.out), "files were written")

    def test_no_arguments_print_the_usage_line(self):
        # source: zero arguments ended in a traceback
        self.refused([], "usage: normalize_events.py WORK_DIR [YYYY-MM-DD]")

    def test_three_arguments_print_the_usage_line(self):
        # source: extra arguments were ignored
        self.refused([self.work, TODAY, "extra"], "usage: normalize_events.py WORK_DIR [YYYY-MM-DD]")

    def test_a_path_that_is_not_a_folder_is_refused(self):
        # source: a typo in the folder looked like an empty week
        missing = os.path.join(self.parent, "nope")
        self.refused([missing], "ERROR: %s is not a folder" % missing)
        afile = os.path.join(self.parent, "afile")
        with open(afile, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.refused([afile], "ERROR: %s is not a folder" % afile)

    def test_a_date_that_is_not_a_calendar_date_is_refused(self):
        # source: a bad date made files named after it
        for value in ("2026-02-30", "2026-13-01", "2026-11-31", "yesterday", "2026-11-2"):
            with self.subTest(value=value):
                self.refused([self.work, value],
                             "date: %s is not a calendar date in YYYY-MM-DD form" % value)

    def test_a_good_command_line_still_runs(self):
        # source: the checks must not refuse a valid call
        proc = _run(SCRIPT, [self.work, TODAY], self.out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "ALL-CITIES.json")))


def _meetup(title, start="2026-11-10T18:00:00-08:00", **fields):
    item = {"eventName": title, "startDateTime": start, "eventShortUrl": "https://example.com/e",
            "organizedByGroup": "Some Group", "venue": {"name": "Hall", "city": "San Diego"},
            "eventDescription": "AI agents and code"}
    item.update(fields)
    return item


class ItemsRun:
    """Writes items files into a work folder, runs the sorter once and reads the results."""

    def __init__(self, files, today=TODAY):
        self.work = tempfile.mkdtemp(prefix="radar-items-work-")
        self.parent = tempfile.mkdtemp(prefix="radar-items-out-")
        self.out = os.path.join(self.parent, "out")
        for name, content in files.items():
            mode = "wb" if isinstance(content, bytes) else "w"
            with open(os.path.join(self.work, name), mode) as fh:
                fh.write(content if isinstance(content, (bytes, str)) else json.dumps(content))
        self.today = today
        self.proc = run_normalize(self.work, self.out, today)

    def close(self):
        shutil.rmtree(self.work, ignore_errors=True)
        shutil.rmtree(self.parent, ignore_errors=True)

    def city(self, slug="san-diego"):
        return load(os.path.join(self.out, slug, self.today + ".json"))

    @property
    def feed(self):
        return load(os.path.join(self.out, "ALL-CITIES.json"))

    def outputs_text(self):
        parts = []
        for path in (os.path.join(self.out, "ALL-CITIES.json"),
                     os.path.join(self.out, "_feed", self.today + ".json"),
                     os.path.join(self.out, "san-diego", self.today + ".json")):
            with open(path, encoding="utf-8") as fh:
                parts.append(fh.read())
        return "\n".join(parts)


class TitlesInAnyScript(unittest.TestCase):
    """Titles in any script, or with no letters at all, merge only when they match."""

    def run_items(self, items, name="items-san-diego-meetup-AI.json"):
        run = ItemsRun({name: items})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-400:])
        return run

    def test_different_non_latin_titles_at_the_same_start_are_both_kept(self):
        # source: the ASCII-only strip gave every such title an empty stem
        run = self.run_items([_meetup("機械学習の会", eventId="a1"),
                              _meetup("Реактивное программирование", eventId="a2")])
        titles = sorted(e["title"] for e in run.city()["events"])
        self.assertEqual(titles, sorted(["機械学習の会", "Реактивное программирование"]))
        self.assertEqual(run.city()["counts"]["deduped"], 0)

    def test_the_same_non_latin_title_at_the_same_start_merges(self):
        # source: cross-listed events in any script are still one event
        run = self.run_items([_meetup("機械学習の会", eventId="a1")],)
        run2 = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("機械学習の会", eventId="a1")],
                         "items-san-diego-meetup-python.json": [_meetup("機械学習の会", eventId="b2")]})
        self.addCleanup(run2.close)
        self.assertEqual(run2.proc.returncode, 0, run2.proc.stderr[-300:])
        self.assertEqual(len(run2.city()["events"]), 1)
        self.assertEqual(run2.city()["counts"]["deduped"], 1)
        self.assertEqual(len(run.city()["events"]), 1)

    def test_punctuation_only_titles_at_the_same_start_are_both_kept_with_different_ids(self):
        # source: an empty stem merged unrelated events and their ids collided
        run = self.run_items([_meetup("!!!"), _meetup("???")])
        events = run.city()["events"]
        self.assertEqual(sorted(e["title"] for e in events), ["!!!", "???"])
        self.assertEqual(len({e["id"] for e in events}), 2)
        self.assertEqual(run.city()["counts"]["deduped"], 0)

    def test_the_same_punctuation_only_listing_from_two_legs_still_merges(self):
        # source: the exact id still identifies one listing
        run = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("!!!")],
                        "items-san-diego-meetup-python.json": [_meetup("!!!")]})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-300:])
        self.assertEqual(len(run.city()["events"]), 1)

    def test_empty_stems_are_kept_apart_on_the_same_day_with_another_host_or_time(self):
        # source: the day tier must not join empty stems either
        run = self.run_items([_meetup("...", start="2026-11-10T18:00:00-08:00"),
                              _meetup("---", start="2026-11-10T19:30:00-08:00", organizedByGroup=None)])
        self.assertEqual(len(run.city()["events"]), 2)

    def test_accents_and_case_still_fold_to_one_stem(self):
        # source: NFKD folding: a cafe with an accent is the same event
        run = self.run_items([_meetup("Café Night", eventId="a1"), _meetup("CAFE night!", eventId="a2")])
        self.assertEqual(len(run.city()["events"]), 1)

    def test_an_ascii_title_keeps_todays_hash_id(self):
        # source: published ids of ASCII titles must not change
        import hashlib
        run = self.run_items([_meetup("Rust Night!")])
        want = "meetup:sha1:" + hashlib.sha1(b"rustnight|2026-11-10T18:00").hexdigest()[:10]
        self.assertEqual([e["id"] for e in run.city()["events"]], [want])

    def test_a_non_latin_title_hash_id_uses_its_letters(self):
        # source: the id hash stem is any-script and cut at 40 characters
        import hashlib
        title = "機械学習の会" + "x" * 50
        run = self.run_items([_meetup(title)])
        stem = ("機械学習の会" + "x" * 50)[:40]
        want = "meetup:sha1:" + hashlib.sha1((stem + "|2026-11-10T18:00").encode("utf-8")).hexdigest()[:10]
        self.assertEqual([e["id"] for e in run.city()["events"]], [want])

    def test_online_events_with_empty_stems_stay_apart_in_the_online_collection(self):
        # source: the online key used the empty stem
        online = {"isOnline": True, "venue": {}}
        run = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("!!!", **online)],
                        "items-los-angeles-meetup-AI.json": [_meetup("???", **online)]})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-300:])
        self.assertEqual(sorted(e["title"] for e in run.feed["online"]), ["!!!", "???"])

    def test_one_online_listing_in_two_cities_is_one_online_event(self):
        # source: the same id in two cities is still the same event
        online = {"isOnline": True, "venue": {}}
        run = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("!!!", **online)],
                        "items-los-angeles-meetup-AI.json": [_meetup("!!!", **online)]})
        self.addCleanup(run.close)
        self.assertEqual(len(run.feed["online"]), 1)
        self.assertEqual(run.feed["online"][0]["cities"], ["los-angeles", "san-diego"])

    def test_radar_events_with_empty_stems_get_no_also_in(self):
        # source: the also_in key used the empty stem
        run = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("!!!")],
                        "items-los-angeles-meetup-AI.json": [_meetup("???")]})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-300:])
        for slug in ("san-diego", "los-angeles"):
            for e in {c["slug"]: c for c in run.feed["cities"]}[slug]["events"]:
                self.assertNotIn("also_in", e)

    def test_radar_events_with_the_same_id_in_two_cities_get_also_in(self):
        # source: the id based key must still link one listing across cities
        run = ItemsRun({"items-san-diego-meetup-AI.json": [_meetup("!!!")],
                        "items-los-angeles-meetup-AI.json": [_meetup("!!!")]})
        self.addCleanup(run.close)
        cities = {c["slug"]: c for c in run.feed["cities"]}
        self.assertEqual(cities["san-diego"]["events"][0]["also_in"], ["los-angeles"])


class BrokenItems(unittest.TestCase):
    """Broken items files and items of the wrong shape are skipped and counted."""

    def good(self, title="Good Meetup", name="items-san-diego-meetup-python.json"):
        return {name: [_meetup(title, eventId="ok1")]}

    def test_a_file_holding_an_error_object_is_skipped_and_its_leg_is_partial(self):
        # source: an actor error body stopped the whole sorting step
        files = self.good()
        files["items-san-diego-meetup-AI.json"] = {"error": "x"}
        run = ItemsRun(files)
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-400:])
        self.assertIn("WARN: items-san-diego-meetup-AI.json: not a JSON list, skipped",
                      run.proc.stderr.splitlines())
        self.assertIs(run.feed["partial"], True)
        self.assertEqual(run.feed["partial_legs"], ["san-diego-meetup-AI"])
        self.assertIs(run.city()["partial"], True)
        self.assertEqual([e["title"] for e in run.city()["events"]], ["Good Meetup"])
        self.assertNotIn("Traceback", run.proc.stderr)

    def test_a_file_that_is_not_utf8_or_not_json_is_skipped_too(self):
        # source: bytes that do not decode must not end the run
        files = self.good()
        files["items-san-diego-meetup-AI.json"] = b"\xff\xfe[1]"
        files["items-san-diego-luma.json"] = "[1, 2"
        run = ItemsRun(files)
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-400:])
        self.assertEqual(sorted(run.feed["partial_legs"]), ["san-diego-luma", "san-diego-meetup-AI"])
        self.assertNotIn("Traceback", run.proc.stderr)

    def test_a_leg_already_listed_as_partial_is_not_listed_twice(self):
        # source: radar.sh also writes the leg to partial-legs.txt
        files = self.good()
        files["items-san-diego-meetup-AI.json"] = "null"
        files["partial-legs.txt"] = "san-diego-meetup-AI\n"
        run = ItemsRun(files)
        self.addCleanup(run.close)
        self.assertEqual(run.feed["partial_legs"], ["san-diego-meetup-AI"])

    def test_items_of_the_wrong_shape_are_skipped_and_counted_per_source(self):
        # source: one odd item stopped every later item of its file
        meetup = ["a string", 7, None, _meetup("Odd venue", venue="a string"),
                  _meetup("Fine One", eventId="ok1")]
        luma = [{"name": "Luma Fine AI", "startAt": "2026-11-10T19:00:00-08:00", "id": "L1",
                 "lumaUrl": "https://example.com/l"},
                {"name": "Odd location", "startAt": "2026-11-10T19:00:00-08:00", "location": "x"}]
        brite = [{"name": "Odd venue", "primary_venue": "x"}, 5]
        run = ItemsRun({"items-san-diego-meetup-AI.json": meetup, "items-san-diego-luma.json": luma,
                        "items-san-diego-eventbrite.json": brite})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-400:])
        lines = run.proc.stderr.splitlines()
        self.assertIn("WARN: san-diego meetup: 4 items skipped (unexpected shape)", lines)
        self.assertIn("WARN: san-diego luma: 1 items skipped (unexpected shape)", lines)
        self.assertIn("WARN: san-diego eventbrite: 2 items skipped (unexpected shape)", lines)
        self.assertNotIn("Traceback", run.proc.stderr)
        self.assertEqual(sorted(e["title"] for e in run.city()["events"]), ["Fine One", "Luma Fine AI"])

    def test_a_clean_run_prints_no_skip_line(self):
        # source: the count line appears only when something was skipped
        run = ItemsRun(self.good())
        self.addCleanup(run.close)
        self.assertNotIn("skipped", run.proc.stderr)


class LinksAreWebLinks(unittest.TestCase):
    """Only http and https links reach the outputs."""

    def test_javascript_and_data_links_reach_none_of_the_json_outputs(self):
        # source: only the Markdown path checked the scheme
        luma = [{"name": "Link Test AI Night", "startAt": "2026-11-10T19:00:00-08:00", "id": "L1",
                 "lumaUrl": "javascript:alert(1)"},
                {"name": "Dup Test AI Night", "startAt": "2026-11-11T19:00:00-08:00", "id": "L2",
                 "lumaUrl": "https://example.com/dup"}]
        meetup = [_meetup("Meetup Test AI Night", eventId="m1", eventShortUrl="data:text/html,<b>x</b>"),
                  _meetup("Dup Test AI Night", start="2026-11-11T19:00:00-08:00", eventId="m2",
                          eventShortUrl="javascript:alert(2)", eventUrl="data:text/plain,y"),
                  _meetup("Online Test AI Night", eventId="m3", isOnline=True, venue={},
                          eventShortUrl="javascript:alert(3)")]
        brite = [{"name": "Brite Test AI Night", "id": "E1", "start_datetime": "2026-11-12T19:00:00-08:00",
                  "url": "data:text/html,z", "summary": "AI talk"}]
        run = ItemsRun({"items-san-diego-luma.json": luma, "items-san-diego-meetup-AI.json": meetup,
                        "items-san-diego-eventbrite.json": brite})
        self.addCleanup(run.close)
        self.assertEqual(run.proc.returncode, 0, run.proc.stderr[-400:])
        text = run.outputs_text()
        self.assertIn("Dup Test AI Night", text)
        self.assertNotIn("javascript:", text)
        self.assertNotIn("data:text", text)
        by_title = {e["title"]: e for e in run.city()["events"]}
        self.assertIsNone(by_title["Link Test AI Night"]["url"])
        self.assertEqual(by_title["Dup Test AI Night"]["url"], "https://example.com/dup")
        self.assertNotIn("also", by_title["Dup Test AI Night"])

    def test_a_good_second_listing_is_kept_as_also_and_a_bad_one_is_dropped(self):
        # source: also holds only web links and is absent when empty
        luma = [{"name": "Dup Test AI Night", "startAt": "2026-11-11T19:00:00-08:00", "id": "L2",
                 "lumaUrl": "https://example.com/dup"}]
        meetup = [_meetup("Dup Test AI Night", start="2026-11-11T19:00:00-08:00", eventId="m2",
                          eventShortUrl="https://example.com/other")]
        run = ItemsRun({"items-san-diego-luma.json": luma, "items-san-diego-meetup-AI.json": meetup})
        self.addCleanup(run.close)
        event = run.city()["events"][0]
        self.assertEqual(event["also"], ["https://example.com/other"])


if __name__ == "__main__":
    unittest.main()
