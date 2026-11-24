"""Resolve test-only exports by their original module and declaration names.

The upstream bundler keeps original-source section comments, but changes chunk
filenames and collision suffixes whenever dependencies move. Restrict discovery
to the server's live import graph, then require exactly one declaration in the
correct source section. An unsupported source layout fails before rewriting the
disposable archive; tests never silently bind a similarly named dependency.
"""

import importlib.util
import json
from pathlib import Path
import posixpath
import re


EFFECT = "node_modules/effect/dist/Effect.js"
AUTH = "src/provider/antigravityAuthSupport.ts"
ACP = "src/provider/acp/AntigravityAcpSupport.ts"
PREFIX = "__t3FlatpakTest_"
SECTIONS = re.compile(r"^// ([^\r\n]+\.(?:[cm]?js|tsx?))\r?$", re.MULTILINE)

# Semantic source names, not output filenames or minified export aliases.
SYMBOLS = {
    **{name: (EFFECT, name) for name in (
        "runPromise", "provide", "provideService", "gen", "succeed", "fail",
        "sync", "scoped", "map", "catch_",
    )},
    "mergeAll": ("node_modules/effect/dist/Layer.js", "mergeAll"),
    "some": ("node_modules/effect/dist/Option.js", "some"),
    "none": ("node_modules/effect/dist/Option.js", "none"),
    "nodeServices": ("node_modules/@effect/platform-node/dist/NodeServices.js", "layer"),
    "digestServices": ("node_modules/effect/dist/crypto/internal/digest.js", "layerWebCrypto"),
    "randomServices": ("node_modules/effect/dist/crypto/SecureRandom.js", "layerWebCrypto"),
    "SecureRandom": ("node_modules/effect/dist/crypto/SecureRandom.js", "SecureRandom"),
    "ChildProcessSpawner": ("node_modules/effect/dist/process/ChildProcessSpawner.js", "ChildProcessSpawner"),
    "cliArchiveTarCommand": ("packages/shared/src/cliRelease.ts", "cliArchiveTarCommand"),
    "reactorMake": ("src/orchestration-v2/PullRequestWatchReactor.ts", "make"),
    "OrchestratorV2": ("src/orchestration-v2/Orchestrator.ts", "OrchestratorV2"),
    "ProjectionStoreV2": ("src/orchestration-v2/ProjectionStore.ts", "ProjectionStoreV2"),
    "PullRequestService": ("src/pullRequest/PullRequestService.ts", "PullRequestService"),
    "ServerBrowserContexts": ("src/preview/ServerBrowserContexts.ts", "ServerBrowserContexts"),
    "captureHtmlScreenshot": ("src/htmlRender/headlessChrome.ts", "captureHtmlScreenshot"),
    "measureHtmlHeights": ("src/htmlRender/headlessChrome.ts", "measureHtmlHeights"),
    "setInputFiles": ("src/preview/ServerBrowserPage.ts", "setInputFiles"),
    **{name: (AUTH, name) for name in (
        "prepareAntigravityProfile", "buildAntigravityAcpSpawnInput",
        "resolveAntigravityInstanceDirectories", "antigravityAuthConfigIssue",
        "isAntigravitySignInRequiredError",
    )},
    **{name: (ACP, name) for name in ("antigravityClientInfo", "makeAntigravityAcpRuntime")},
}


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "preview_patch", Path(__file__).resolve().parents[1] / "scripts/patch-preview-browser.py"
    )
    patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patch)
    return patch


def source_sections(source):
    markers = list(SECTIONS.finditer(source))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
        yield marker[1], source[marker.end():end]


def resolve_symbols(modules, active_paths, contracts=SYMBOLS):
    syntax_matches = load_patcher().syntax_matches
    sections = [
        (path, section_name, body)
        for path in sorted(active_paths)
        for section_name, body in source_sections(modules[path])
    ]
    bindings = {}
    for name, (source_name, base) in contracts.items():
        declaration = re.compile(
            r"^(?:export\s+)?(?:const|let|var|function|class)\s+("
            + re.escape(base) + r"(?:\$\d+)?)(?![A-Za-z0-9_$])",
            re.MULTILINE,
        )
        matches = [
            (path, match[1])
            for path, section_name, body in sections
            if section_name == source_name or section_name.endswith("/" + source_name)
            for match in syntax_matches(body, declaration)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Runtime test needs exactly one live {source_name} declaration for {base}; "
                f"found {len(matches)}"
            )
        path, symbol = matches[0]
        bindings[name] = {"path": path, "symbol": symbol, "export": PREFIX + name}
    return bindings


def relative_import(source_path, target_path):
    relative = posixpath.relpath(target_path, posixpath.dirname(source_path))
    return relative if relative.startswith(".") else "./" + relative


def binding_module(bindings, names, archive_base):
    imports = {}
    for name in sorted(set(names)):
        binding = bindings[name]
        imports.setdefault(binding["path"], []).append(f'{binding["export"]} as {name}')
    return "".join(
        "import {" + ", ".join(symbols) + "} from "
        + json.dumps(archive_base.rstrip("/") + "/" + path) + ";\n"
        for path, symbols in sorted(imports.items())
    )


def build_instrumentation(resolved, archive_base):
    modules = resolved["sources"]
    active = resolved["active_paths"]
    if any(PREFIX in modules[path] for path in active):
        raise ValueError("Runtime test archive is already instrumented")
    bindings = resolve_symbols(modules, active)
    suffixes = {}
    for binding in bindings.values():
        suffixes.setdefault(binding["path"], []).append(
            f'export {{{binding["symbol"]} as {binding["export"]}}};\n'
        )

    # Exercise the shipped notification-routing block, rather than a copy of
    # its policy. Its original imports stay in scope. Only our wrapper needs a
    # newly resolved Effect.gen binding, whose upstream alias is immaterial.
    runtime = resolved["runtime"]
    source = modules[runtime]
    start = "\t\tconst noticeKind = command.notification?.source.kind;"
    end = "\t\tconst notification = command.manualContinuationOfRunId === void 0 ?"
    if source.count(start) != 1 or source.count(end) != 1 or source.index(end) <= source.index(start):
        raise ValueError("Runtime test needs exactly one notification routing block")
    policy = source[source.index(start):source.index(end)]
    gen = bindings["gen"]
    if gen["path"] == runtime:
        generator = gen["symbol"]
    else:
        generator = PREFIX + "policyGen"
        suffixes.setdefault(runtime, []).append(
            f'import {{{gen["export"]} as {generator}}} from '
            + json.dumps(relative_import(runtime, gen["path"])) + ";\n"
        )
    policy_export = PREFIX + "prWatchDelivery"
    suffixes.setdefault(runtime, []).append(
        f"export const {policy_export} = (input) => {generator}(function* () {{\n"
        "const {command, projection, providerSessions, stopReachedRun, freshContext, delegatedCompletion} = input;\n"
        "let dispatchMode = input.dispatchMode;\n"
        + policy + "\nreturn dispatchMode;\n});\n"
    )

    preview_names = [
        "ServerBrowserContexts", "captureHtmlScreenshot", "measureHtmlHeights",
        "setInputFiles", "nodeServices", "runPromise", "provide",
    ]
    preview = binding_module(bindings, preview_names, archive_base) + (
        "export {ServerBrowserContexts, captureHtmlScreenshot, measureHtmlHeights, "
        "setInputFiles, nodeServices as previewTestNodeServices, runPromise, provide};\n"
    )
    pr_fields = {
        "make": "reactorMake", **{name: name for name in (
            "OrchestratorV2", "ProjectionStoreV2", "PullRequestService", "SecureRandom",
            "provideService", "gen", "succeed", "fail", "sync",
        )},
    }
    pr = binding_module(bindings, [*pr_fields.values(), "runPromise", "some", "none"], archive_base)
    pr += "export {runPromise};\nexport const t = {" + ", ".join(
        f"{key}: {value}" for key, value in pr_fields.items()
    ) + "};\nexport const option = {some, none};\n"
    pr += f"export {{{policy_export} as prWatchDeliveryTest}} from " + json.dumps(
        archive_base.rstrip("/") + "/" + runtime
    ) + ";\n"

    agy_fields = {name: name for name in (
        "prepareAntigravityProfile", "buildAntigravityAcpSpawnInput",
        "resolveAntigravityInstanceDirectories", "antigravityClientInfo",
        "antigravityAuthConfigIssue", "isAntigravitySignInRequiredError",
        "makeAntigravityAcpRuntime", "ChildProcessSpawner", "cliArchiveTarCommand",
        "gen", "scoped", "map", "succeed",
    )}
    agy_fields["catch"] = "catch_"
    agy = binding_module(bindings, [
        *agy_fields.values(), "runPromise", "provide", "mergeAll", "nodeServices",
        "digestServices", "randomServices",
    ], archive_base)
    agy += "export {runPromise, provide};\nexport const t = {" + ", ".join(
        f"{key}: {value}" for key, value in agy_fields.items()
    ) + "};\nexport const services = mergeAll(nodeServices, digestServices, randomServices);\n"

    replacements = {
        path: (modules[path] + "\n" + "".join(suffix)).encode()
        for path, suffix in suffixes.items()
    }
    return replacements, {
        "preview-test-bindings.mjs": preview,
        "pr-watch-test-bindings.mjs": pr,
        "antigravity-test-bindings.mjs": agy,
    }


def instrument_archive(archive, output_directory, archive_base):
    patch = load_patcher()
    resolved = patch.resolve_archive(archive)
    replacements, generated = build_instrumentation(resolved, archive_base)
    patch.rewrite_archive(archive, replacements)
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, source in generated.items():
        (output_directory / filename).write_text(source)
