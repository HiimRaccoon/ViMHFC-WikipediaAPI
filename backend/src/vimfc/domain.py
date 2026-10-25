"""Portable values, canonical fingerprints, and typed operational failures."""
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal, TypedDict

GROUPS = ('Person', 'Organization', 'Location', 'Event', 'Creative Work',
          'Award / Competition', 'Landmark / Facility', 'Science / Technology')
CORPUS_SIZE = 20_000


class VimfcError(Exception):
    exit_code = 1


class InvalidConfig(VimfcError):
    exit_code = 2


class Incompatible(VimfcError):
    exit_code = 3


class InvalidCorpus(VimfcError):
    exit_code = 4


class SourceUnavailable(VimfcError):
    def __init__(self, message, *, retryable=False, code='source_unavailable', retry_at=None, details=None, partial_items=None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.retry_at = retry_at
        self.details = details or {}
        self.partial_items = partial_items or {}


def canonical(value):
    return json.dumps(asdict(value) if is_dataclass(value) else value,
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def utc(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('UTC timestamp requires an explicit timezone')
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def at_or_before(value, cutoff):
    return datetime.fromisoformat(utc(value).replace('Z', '+00:00')) <= datetime.fromisoformat(utc(cutoff).replace('Z', '+00:00'))


@dataclass(frozen=True)
class SourceRevisionRef:
    site: str
    identity: str
    page_id: int
    title: str
    revision_id: int
    revision_timestamp: str
    snapshot: str
    lookup_timestamp: str | None = None


@dataclass(frozen=True)
class RawRevision:
    ref: SourceRevisionRef
    content: str
    content_model: str
    source_sha1: str
    local_sha256: str
    cache_ref: dict = field(default_factory=dict)


@dataclass
class LeadExtractionResult:
    outcome: str
    extraction_version: str
    full_lead: str = ''
    paragraphs: list[str] = field(default_factory=list)
    sentences: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    template_usage: dict = field(default_factory=dict)


@dataclass
class ClassificationResult:
    outcome: str
    primary_entity_type: str | None = None
    subtypes: list[dict] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    reason: str | None = None
    diagnostics: list[dict] = field(default_factory=list)


@dataclass
class PrimaryFirstResult(ClassificationResult):
    primary_status: str = 'unresolved'
    primary_provenance: list[dict] = field(default_factory=list)
    primary_candidates: list[dict] = field(default_factory=list)
    primary_decision: str | None = None
    subtype_status: str = 'not_attempted'
    subtype_search_status: str = 'not_run'
    subtype_provenance: list[dict] = field(default_factory=list)
    policy_version: str = 'primary-first-1'


@dataclass
class PairManifest:
    pairs: list[dict]
    omitted: dict
    ranking_version: str


@dataclass
class SelectionManifest:
    page_ids: list[int]
    steps: list[dict]
    outcome: str
    shortage: int = 0


class EvidenceKey(TypedDict):
    page_id: int
    revision_id: int
    sent_id: int


class Evidence(TypedDict):
    article_title: str
    page_id: int
    revision_id: int
    source_url: str
    sent_id: int
    evidence_text: str


class BenchmarkRecord(TypedDict):
    id: str
    session_id: str
    corpus_version: str
    claim: str
    label: Literal['SUPPORTED', 'REFUTED', 'NEI']
    hop_count: int
    gold_evidence: list[Evidence]
    partial_evidence: list[Evidence]
    metadata: dict
    reasoning_chain: None
    created_at: str
