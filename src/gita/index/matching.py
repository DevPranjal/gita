"""F2 record-only classification of caller-accepted transitions, not automatic matching."""

from __future__ import annotations

from .model import (
	EntityDefinition, EntityOccurrence, EntityVersion, _identifier, _oids,
	_require, content_id,
)


def _uncovered_own(version: EntityVersion) -> tuple[tuple[str, str | None], ...] | None:
	"""Preserve residual placement around unambiguous, compressed coverage runs."""
	own = version.own
	if own is None:
		return None
	signature, body = version.signature, version.body
	if own in (signature, body):
		return ()
	if (signature is not None and body is not None
			and (own == signature + body or own == body + signature)):
		return ()

	residual = list(range(len(own)))
	labels = ["own"] * len(own)
	for label, part in (("signature", signature), ("body", body)):
		if not part:
			continue
		positions = []
		matched = 0
		for index in residual:
			if matched < len(part) and own[index] == part[matched]:
				positions.append(index)
				matched += 1
		if matched != len(part):
			return None
		matched -= 1
		for index in reversed(residual):
			if matched >= 0 and own[index] == part[matched]:
				if positions[matched] != index:
					return None
				matched -= 1
		removed = set(positions)
		for index in positions:
			labels[index] = label
		residual = [index for index in residual if index not in removed]
	if not residual:
		return ()

	markers: list[tuple[str, str | None]] = []
	for label, token in zip(labels, own):
		marker = (label, token if label == "own" else None)
		if label == "own" or not markers or markers[-1] != marker:
			markers.append(marker)
	return tuple(markers)


def classify_transition(
	source: EntityOccurrence,
	destination: EntityOccurrence,
	source_definition: EntityDefinition,
	destination_definition: EntityDefinition,
	source_version: EntityVersion,
	destination_version: EntityVersion,
	*,
	source_parent_identity: str | None,
	destination_parent_identity: str | None,
) -> tuple[str, ...]:
	"""Classify records whose adjacency and continuity the caller already accepted.

	Non-root parent identities must be known accepted bindings, not parse-local IDs.
	These flags neither establish lineage nor prove semantic equivalence.
	"""
	for occurrence, definition, version, parent_identity in (
		(source, source_definition, source_version, source_parent_identity),
		(destination, destination_definition, destination_version, destination_parent_identity),
	):
		_require(type(occurrence) is EntityOccurrence, "expected EntityOccurrence")
		_require(type(definition) is EntityDefinition, "expected EntityDefinition")
		_require(type(version) is EntityVersion, "expected EntityVersion")
		occurrence.__post_init__()
		definition.__post_init__()
		version.__post_init__()
		occurrence.commit.__post_init__()
		occurrence.blob.__post_init__()
		definition.span.__post_init__()
		_require(occurrence.object_type == "blob" and occurrence.mode in (0o100644, 0o100755),
				 "transitions require regular blob entries")
		_require(occurrence.definition_id == definition.id, "occurrence definition mismatch")
		_require(definition.version_id == version.id, "definition version mismatch")
		_require(definition.parse_id == content_id(
			"par1", (version.parse_fingerprint, occurrence.blob.fields(), occurrence.path),
		), "definition parse context mismatch")
		_require(definition.kind == version.kind, "definition and version kinds must agree")
		_require(definition.synthetic_root == (definition.kind == "module"),
				 "only synthetic roots have module kind")
		if parent_identity is None:
			_require(definition.synthetic_root, "non-module parents require accepted identities")
		else:
			_require(not definition.synthetic_root, "synthetic modules cannot have parents")
			_identifier(parent_identity, ("eid1", "oeid1"))
	_oids(source.commit, destination.commit)
	for field_name in ("parse_fingerprint", "language", "kind"):
		_require(getattr(source_version, field_name) == getattr(destination_version, field_name),
				 f"transition {field_name} must agree")

	flags: set[str] = set()
	if source_definition.names[-1] != destination_definition.names[-1]:
		flags.add("renamed")
	if source.path != destination.path or source_parent_identity != destination_parent_identity:
		flags.add("moved")
	if source.mode != destination.mode:
		flags.add("mode_changed")

	changed: set[str] = set()
	for field_name in ("own", "signature", "body", "subtree"):
		before = getattr(source_version, field_name)
		after = getattr(destination_version, field_name)
		if before is None or after is None:
			if field_name != "signature" or not source_definition.synthetic_root:
				flags.add("representation_unknown")
		elif before != after:
			changed.add(field_name)
	for field_name in ("signature", "body"):
		if field_name in changed:
			flags.add(field_name + "_changed")
	if "own" in changed:
		before_residual = _uncovered_own(source_version)
		after_residual = _uncovered_own(destination_version)
		if (not {"signature", "body"} & changed
				or before_residual is None or after_residual is None
				or before_residual != after_residual):
			flags.add("own_content_changed")
	if ("subtree" in changed and source_version.own is not None
			and source_version.own == destination_version.own):
		flags.add("descendant_changed")
	if (not changed and "representation_unknown" not in flags
			and source_version.source_digest != destination_version.source_digest):
		flags.add("cosmetic")
	return tuple(sorted(flags))