#!/usr/bin/env python3
"""Check the unsigned GitHub Pages artifact handoff without signing secrets."""

from pathlib import Path
import re


WORKFLOW = Path(".github/workflows/pages-flatpak-repo.yml")


def step(workflow: str, name: str) -> str:
    heading = f"      - name: {name}\n"
    start = workflow.find(heading)
    if start == -1:
        raise AssertionError(f"Missing workflow step: {name}")
    body_start = start + len(heading)
    boundary = re.search(r"(?m)^(?: {0,5}\S| {6}- )", workflow[body_start:])
    end = body_start + boundary.start() if boundary else None
    return workflow[start:end]


def require(text: str, fragments: tuple[str, ...], context: str) -> None:
    lines = {line.strip() for line in text.splitlines()}
    for fragment in fragments:
        if fragment not in lines:
            raise AssertionError(f"{context}: missing {fragment!r}")


def main() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    architectures = re.findall(r"^          - arch: (\S+)\s*$", source, re.MULTILINE)
    assert architectures == ["x86_64"], f"Update this contract for: {architectures}"

    require(step(source, "Upload unsigned architecture repository"), (
        "uses: actions/upload-artifact@v7",
        "name: t3code-ostree-${{ matrix.variant.arch }}",
        "t3code-ostree-${{ matrix.variant.arch }}.tar.gz",
        "t3code-ref-${{ matrix.variant.arch }}.txt",
        "t3code-commit-${{ matrix.variant.arch }}.txt",
        "if-no-files-found: error",
    ), "producer")

    download = step(source, "Download unsigned architecture repository")
    require(download, (
        "uses: actions/download-artifact@v8",
        "name: t3code-ostree-x86_64",
        "path: inputs/t3code-ostree-x86_64",
    ), "download")
    assert not re.search(r"^\s*(?:pattern|merge-multiple):", download, re.MULTILINE), (
        "Download must use the exact single-artifact path, not a wildcard"
    )

    consumer = step(source, "Validate, sign, and aggregate exact application ref")
    require(consumer, (
        "arch=x86_64",
        'artifact_dir="inputs/t3code-ostree-${arch}"',
        'archive="$artifact_dir/t3code-ostree-${arch}.tar.gz"',
        'ref_file="$artifact_dir/t3code-ref-${arch}.txt"',
        'commit_file="$artifact_dir/t3code-commit-${arch}.txt"',
        'expected_ref="app/${APP_ID}/${arch}/master"',
        'test -s "$archive" || { echo "Missing repository archive: $archive" >&2; exit 1; }',
        'test -s "$ref_file" || { echo "Missing application ref: $ref_file" >&2; exit 1; }',
        'test "$(<"$ref_file")" = "$expected_ref"',
        'test -s "$commit_file"',
        'validated_commit="$(<"$commit_file")"',
        '[[ "$validated_commit" =~ ^[a-f0-9]{64}$ ]]',
        'test "$(ostree rev-parse --repo="$source_repo" "$expected_ref")" = "$validated_commit"',
        'test "$(ostree rev-parse --repo=site/repo "$expected_ref")" = "$validated_commit"',
    ), "consumer")
    assert consumer.index('= "$validated_commit"') < consumer.index("flatpak build-sign"), (
        "The signer must verify the tested commit before signing"
    )
    gate = step(source, "Check installed repository bundle")
    require(gate, (
        "T3_DISPOSABLE_ACCOUNT: '1'",
        'bash tests/check-publication.sh installed "$bundle"',
    ), "installed gate")
    assert source.index(gate) < source.index(step(source, "Archive validated architecture repository"))
    print("Pages x86_64 validated commit / artifact handoff contract: OK")


if __name__ == "__main__":
    main()
