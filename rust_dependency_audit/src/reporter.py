# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************

"""Multi-format report generator (Markdown, JSON, HTML/GitHub Pages)."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import json

from .auditor import OrganizationAuditReport


def generate_json_report(report: OrganizationAuditReport) -> str:
    """Serializes the audit report into formatted JSON."""
    data = {
        "organization": report.organization,
        "reference_repo": report.reference_repo,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_repositories": report.total_repositories,
        "rust_repositories_count": report.rust_repositories_count,
        "total_distinct_crates": report.total_distinct_crates,
        "managed_crates_count": report.managed_crates_count,
        "mismatch_crates_count": report.mismatch_crates_count,
        "unmanaged_crates_count": report.unmanaged_crates_count,
        "crate_usage_summary": {
            name: {
                "crate_name": usage.crate_name,
                "status": usage.status.value,
                "score_crates_version": usage.score_crates_version,
                "used_in_repos": usage.used_in_repos,
                "versions_seen": sorted(usage.versions_seen),
            }
            for name, usage in report.crate_usage_summary.items()
        },
        "repositories": [
            {
                "repo_name": repo.repo_name,
                "is_archived": repo.is_archived,
                "project_paths": repo.project_paths,
                "managed_crates_count": repo.managed_crates_count,
                "mismatch_crates_count": repo.mismatch_crates_count,
                "unmanaged_crates_count": repo.unmanaged_crates_count,
                "crates": [
                    {
                        "crate_name": c.crate_name,
                        "status": c.status.value,
                        "requested_version": c.requested_version,
                        "resolved_version": c.resolved_version,
                        "score_crates_version": c.score_crates_version,
                        "features": c.features,
                        "dep_types": c.dep_types,
                        "manifest_paths": c.manifest_paths,
                    }
                    for c in repo.crates
                ],
            }
            for repo in report.repositories
        ],
    }
    return json.dumps(data, indent=2)


def generate_markdown_report(report: OrganizationAuditReport) -> str:
    """Generates a clean Markdown report summarizing the audit."""
    ref_repo = report.reference_repo or "eclipse-score/score-crates"
    ref_url = (
        ref_repo if ref_repo.startswith("http") else f"https://github.com/{ref_repo}"
    )
    ref_short = ref_repo.split("/")[-1]

    lines: list[str] = [
        f"# Rust Dependency Audit Report: {report.organization or 'Organization'}",
        "",
        f"> **Generated at:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`  ",
        f"> **Central Reference:** [`{ref_repo}`]({ref_url})",
        "",
        "## Executive Summary",
        "",
        "| Metric | Count |",
        "| :--- | :---: |",
        f"| **Total Repositories Scanned** | {report.total_repositories} |",
        f"| **Repositories using Rust** | {report.rust_repositories_count} |",
        f"| **Total Distinct Crates** | {report.total_distinct_crates} |",
        f"| **Managed in {ref_short}** | {report.managed_crates_count} |",
        f"| **Version Mismatches** | {report.mismatch_crates_count} |",
        f"| **Unmanaged Crates** | {report.unmanaged_crates_count} |",
        "",
        "---",
        "",
        "## Terminology & Classification Guide",
        "",
        f"- 🟢 **`MANAGED`**: The crate is officially registered and maintained in [`{ref_repo}`]({ref_url}) (the central source of truth), and the repository requested version matches the centralized version.",
        f"- 🟡 **`VERSION_MISMATCH`**: The crate is registered in `{ref_short}`, but this repository specifies or locks a **different version** (e.g. repository uses `4.5.37` while `{ref_short}` provides `4.5.4`). **Action:** Align the repository dependency or update `{ref_short}` so all S-CORE modules share a unified version.",
        f"- 🔴 **`UNMANAGED`**: The crate is used by the repository as a direct external dependency, but is **NOT yet registered** in `{ref_short}`. **Action:** Onboard this crate into `{ref_short}` via `crate.spec()` in `MODULE.bazel` to establish central tracking.",
        "",
        "---",
        "",
        "## Repositories Overview",
        "",
        "| Repository | Projects | Total Crates | Status Breakdown | Compliance |",
        "| :--- | :---: | :---: | :--- | :---: |",
    ]

    for repo in sorted(report.repositories, key=lambda r: r.repo_name):
        total_repo_crates = len(repo.crates)
        pct = (
            round((repo.managed_crates_count / total_repo_crates) * 100, 1)
            if total_repo_crates > 0
            else 0
        )
        breakdown = (
            f"**{repo.managed_crates_count}** managed, "
            f"**{repo.mismatch_crates_count}** mismatch, "
            f"**{repo.unmanaged_crates_count}** unmanaged"
        )
        lines.append(
            f"| **`{repo.repo_name}`** | {len(repo.project_paths)} | {total_repo_crates} | {breakdown} | {pct}% |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Crate Usage Overview",
            "",
            f"| Crate | Status | `{ref_short}` Version | Repositories Using | Versions Requested |",
            "| :--- | :---: | :---: | :--- | :--- |",
        ]
    )

    for crate_name, usage in sorted(report.crate_usage_summary.items()):
        status_badge = f"`{usage.status.value}`"
        score_ver = (
            f"`{usage.score_crates_version}`" if usage.score_crates_version else "-"
        )
        repos_str = ", ".join(
            f"`{r.split('/')[-1]}`" for r in sorted(usage.used_in_repos)
        )
        versions_str = ", ".join(f"`{v}`" for v in sorted(usage.versions_seen)) or "-"
        lines.append(
            f"| **{crate_name}** | {status_badge} | {score_ver} | {repos_str} | {versions_str} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Repository Breakdown",
            "",
        ]
    )

    for repo in sorted(report.repositories, key=lambda r: r.repo_name):
        lines.append(f"### {repo.repo_name}")
        lines.append("")
        if repo.is_archived:
            lines.append("*(Archived Repository)*\n")

        lines.append(f"- **Manifests / Projects ({len(repo.project_paths)}):**")
        for path in repo.project_paths:
            lines.append(f"  - `{path}`")
        lines.append("")

        if not repo.crates:
            lines.append("*No external crate dependencies found.*")
            lines.append("")
            continue

        lines.append(
            f"**Crates ({len(repo.crates)}) — Managed: {repo.managed_crates_count}, Mismatch: {repo.mismatch_crates_count}, Unmanaged: {repo.unmanaged_crates_count}**"
        )
        lines.append("")
        lines.append(
            f"| Crate | Status | Requested | Resolved | `{ref_short}` | Manifest |"
        )
        lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")

        for c in repo.crates:
            c_score_ver = (
                f"`{c.score_crates_version}`" if c.score_crates_version else "-"
            )
            c_req_ver = f"`{c.requested_version}`" if c.requested_version else "-"
            c_res_ver = f"`{c.resolved_version}`" if c.resolved_version else "-"
            manifests_str = ", ".join(f"`{m}`" for m in c.manifest_paths) or "-"
            lines.append(
                f"| **{c.crate_name}** | `{c.status.value}` | {c_req_ver} | {c_res_ver} | {c_score_ver} | {manifests_str} |"
            )

        lines.append("")

    return "\n".join(lines)


def generate_html_report(report: OrganizationAuditReport) -> str:
    """Generates a self-contained responsive HTML dashboard for GitHub Pages."""
    org_title = html.escape(report.organization or "Eclipse S-CORE")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    ref_repo = report.reference_repo or "eclipse-score/score-crates"
    ref_url = (
        ref_repo if ref_repo.startswith("http") else f"https://github.com/{ref_repo}"
    )
    ref_name_escaped = html.escape(ref_repo)
    ref_url_escaped = html.escape(ref_url)
    ref_short_escaped = html.escape(ref_repo.split("/")[-1])

    # Metrics
    total_crates = report.total_distinct_crates
    managed_pct = (
        round((report.managed_crates_count / total_crates) * 100, 1)
        if total_crates > 0
        else 0
    )

    # Convert report to JSON for client-side search/filtering
    json_data = generate_json_report(report)
    json_safe = json_data.replace("<", "\\u003c")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rust Dependency Audit — {org_title}</title>
  <style>
    :root {{
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --success: #3fb950;
      --warning: #d29922;
      --danger: #f85149;
      --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: var(--font-family);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    header {{
      max-width: 1200px;
      margin: 0 auto 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }}
    h1 {{ font-size: 28px; font-weight: 600; color: #f0f6fc; margin-bottom: 8px; }}
    .meta {{ font-size: 14px; color: var(--text-muted); }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .cards-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }}
    .card-title {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
    .card-value {{ font-size: 28px; font-weight: bold; color: #f0f6fc; margin-top: 4px; }}
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 12px;
      text-transform: uppercase;
    }}
    .badge-managed {{ background-color: rgba(63, 185, 80, 0.15); color: var(--success); border: 1px solid var(--success); }}
    .badge-mismatch {{ background-color: rgba(210, 153, 34, 0.15); color: var(--warning); border: 1px solid var(--warning); }}
    .badge-unmanaged {{ background-color: rgba(248, 81, 73, 0.15); color: var(--danger); border: 1px solid var(--danger); }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 20px;
      align-items: center;
      justify-content: space-between;
    }}
    .search-box {{
      flex: 1;
      min-width: 280px;
      padding: 8px 12px;
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      font-size: 14px;
    }}
    .filters {{ display: flex; gap: 8px; }}
    .filter-btn {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 13px;
      cursor: pointer;
    }}
    .filter-btn.active {{
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }}
    .section-title {{ font-size: 20px; font-weight: 600; margin: 24px 0 12px; color: #f0f6fc; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 24px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 12px 16px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }}
    th {{ background: #21262d; color: #f0f6fc; font-weight: 600; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background-color: rgba(255, 255, 255, 0.02); }}
    code {{
      background-color: rgba(110, 118, 129, 0.2);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
      font-size: 13px;
    }}
    .repo-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .repo-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}
    .repo-name {{ font-size: 18px; font-weight: 600; color: var(--accent); text-decoration: none; }}
    .paths-list {{ font-size: 13px; color: var(--text-muted); margin-bottom: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>🦀 Rust Dependency Audit — {org_title}</h1>
    <div class="meta">
      Generated: <strong>{timestamp}</strong> | Central Single Source of Truth: <a href="{ref_url_escaped}" target="_blank" style="color: var(--accent);">{ref_name_escaped}</a>
    </div>
  </header>

  <div class="container">
    <div class="cards-grid">
      <div class="card">
        <div class="card-title">Repositories Scanned</div>
        <div class="card-value">{report.total_repositories}</div>
      </div>
      <div class="card">
        <div class="card-title">Rust Repositories</div>
        <div class="card-value">{report.rust_repositories_count}</div>
      </div>
      <div class="card">
        <div class="card-title">Distinct Crates</div>
        <div class="card-value">{report.total_distinct_crates}</div>
      </div>
      <div class="card">
        <div class="card-title">Managed in {ref_short_escaped}</div>
        <div class="card-value" style="color: var(--success);">{report.managed_crates_count}</div>
      </div>
      <div class="card">
        <div class="card-title">Version Mismatches</div>
        <div class="card-value" style="color: var(--warning);">{report.mismatch_crates_count}</div>
      </div>
      <div class="card">
        <div class="card-title">Unmanaged Crates</div>
        <div class="card-value" style="color: var(--danger);">{report.unmanaged_crates_count}</div>
      </div>
      <div class="card">
        <div class="card-title">Compliance Rate</div>
        <div class="card-value">{managed_pct}%</div>
      </div>
    </div>

    <div class="card" style="margin-bottom: 24px; border-left: 4px solid var(--accent);">
      <div style="font-weight: 600; font-size: 16px; margin-bottom: 8px; color: #f0f6fc;">💡 Terminology & Classification Guide</div>
      <ul style="list-style: none; display: flex; flex-direction: column; gap: 8px; font-size: 14px;">
        <li><span class="badge badge-managed">MANAGED</span> <strong>Registered & Aligned:</strong> The crate is defined in <code>{ref_short_escaped}</code> (central single source of truth) and the repository's requested version matches.</li>
        <li><span class="badge badge-mismatch">VERSION_MISMATCH</span> <strong>Version Discrepancy:</strong> The crate is in <code>{ref_short_escaped}</code>, but this repository specifies or locks a different version. <em>Action: Align repository dependency or update {ref_short_escaped}.</em></li>
        <li><span class="badge badge-unmanaged">UNMANAGED</span> <strong>Missing from {ref_short_escaped}:</strong> The crate is used as a direct external dependency but is not yet registered in <code>{ref_short_escaped}</code>. <em>Action: Onboard crate into {ref_short_escaped} via crate.spec().</em></li>
      </ul>
    </div>

    <div class="controls">
      <input type="text" id="searchInput" class="search-box" placeholder="Search by crate or repository name..." oninput="filterData()">
      <div class="filters">
        <button class="filter-btn active" onclick="setStatusFilter('ALL', this)">All</button>
        <button class="filter-btn" onclick="setStatusFilter('MANAGED', this)">Managed</button>
        <button class="filter-btn" onclick="setStatusFilter('VERSION_MISMATCH', this)">Mismatch</button>
        <button class="filter-btn" onclick="setStatusFilter('UNMANAGED', this)">Unmanaged</button>
      </div>
    </div>

    <div class="section-title">📂 Repositories Overview</div>
    <table>
      <thead>
        <tr>
          <th>Repository</th>
          <th>Rust Projects</th>
          <th>Total Crates</th>
          <th>Status Breakdown</th>
          <th>Compliance</th>
        </tr>
      </thead>
      <tbody id="reposOverviewBody">
      </tbody>
    </table>

    <div class="section-title">📦 Crates Usage Overview</div>
    <table>
      <thead>
        <tr>
          <th>Crate Name</th>
          <th>Status</th>
          <th><code>{ref_short_escaped}</code> Version</th>
          <th>Used in Repositories</th>
          <th>Requested Versions</th>
        </tr>
      </thead>
      <tbody id="cratesTableBody">
      </tbody>
    </table>

    <div class="section-title">📑 Detailed Repository Breakdown</div>
    <div id="reposContainer"></div>
  </div>

  <script id="audit-data" type="application/json">
{json_safe}
  </script>

  <script>
    function escapeHtml(str) {{
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}

    const reportData = JSON.parse(document.getElementById('audit-data').textContent);
    let currentStatus = 'ALL';

    function setStatusFilter(status, btn) {{
      currentStatus = status;
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterData();
    }}

    function filterData() {{
      const query = document.getElementById('searchInput').value.toLowerCase().trim();
      renderReposOverview(query, currentStatus);
      renderCrates(query, currentStatus);
      renderRepos(query, currentStatus);
    }}

    function renderReposOverview(query, statusFilter) {{
      const tbody = document.getElementById('reposOverviewBody');
      tbody.innerHTML = '';

      for (const repo of reportData.repositories) {{
        const matchesQuery = !query || repo.repo_name.toLowerCase().includes(query) || repo.crates.some(c => c.crate_name.toLowerCase().includes(query));
        if (!matchesQuery) continue;

        const filteredCrates = repo.crates.filter(c => statusFilter === 'ALL' || c.status === statusFilter);
        if (statusFilter !== 'ALL' && filteredCrates.length === 0) continue;

        const totalCrates = repo.crates.length;
        const compliance = totalCrates > 0 ? Math.round((repo.managed_crates_count / totalCrates) * 1000) / 10 : 0;

        const tr = document.createElement('tr');
        const safeRepoName = escapeHtml(repo.repo_name);
        tr.innerHTML = `
          <td><a href="#repo-${{safeRepoName}}" style="color: var(--accent); text-decoration: none; font-weight: 600;">${{safeRepoName}}</a></td>
          <td><code>${{repo.project_paths.length}}</code></td>
          <td><strong>${{totalCrates}}</strong></td>
          <td>
            <span class="badge badge-managed">${{repo.managed_crates_count}} managed</span>
            <span class="badge badge-mismatch">${{repo.mismatch_crates_count}} mismatch</span>
            <span class="badge badge-unmanaged">${{repo.unmanaged_crates_count}} unmanaged</span>
          </td>
          <td><strong>${{compliance}}%</strong></td>
        `;
        tbody.appendChild(tr);
      }}
    }}

    function renderCrates(query, statusFilter) {{
      const tbody = document.getElementById('cratesTableBody');
      tbody.innerHTML = '';

      const crates = reportData.crate_usage_summary;
      const sortedKeys = Object.keys(crates).sort();

      for (const crateName of sortedKeys) {{
        const item = crates[crateName];
        if (statusFilter !== 'ALL' && item.status !== statusFilter) continue;
        if (query && !crateName.toLowerCase().includes(query) && !item.used_in_repos.some(r => r.toLowerCase().includes(query))) continue;

        const tr = document.createElement('tr');
        const badgeClass = item.status === 'MANAGED' ? 'badge-managed' : (item.status === 'VERSION_MISMATCH' ? 'badge-mismatch' : 'badge-unmanaged');

        const safeCrate = escapeHtml(crateName);
        const safeScoreVer = item.score_crates_version ? '<code>' + escapeHtml(item.score_crates_version) + '</code>' : '-';
        const safeRepos = item.used_in_repos.map(r => '<code>' + escapeHtml(r.split('/').pop()) + '</code>').join(', ');
        const safeVersions = item.versions_seen.map(v => '<code>' + escapeHtml(v) + '</code>').join(', ') || '-';

        tr.innerHTML = `
          <td><strong>${{safeCrate}}</strong></td>
          <td><span class="badge ${{badgeClass}}">${{escapeHtml(item.status)}}</span></td>
          <td>${{safeScoreVer}}</td>
          <td>${{safeRepos}}</td>
          <td>${{safeVersions}}</td>
        `;
        tbody.appendChild(tr);
      }}
    }}

    function renderRepos(query, statusFilter) {{
      const container = document.getElementById('reposContainer');
      container.innerHTML = '';

      for (const repo of reportData.repositories) {{
        const matchesQuery = !query || repo.repo_name.toLowerCase().includes(query) || repo.crates.some(c => c.crate_name.toLowerCase().includes(query));
        if (!matchesQuery) continue;

        const filteredCrates = repo.crates.filter(c => statusFilter === 'ALL' || c.status === statusFilter);
        if (statusFilter !== 'ALL' && filteredCrates.length === 0) continue;

        const card = document.createElement('div');
        card.className = 'repo-card';
        card.id = `repo-${{escapeHtml(repo.repo_name)}}`;

        let cratesRows = filteredCrates.map(c => {{
          const badgeClass = c.status === 'MANAGED' ? 'badge-managed' : (c.status === 'VERSION_MISMATCH' ? 'badge-mismatch' : 'badge-unmanaged');
          const safeCrate = escapeHtml(c.crate_name);
          const safeReq = c.requested_version ? '<code>' + escapeHtml(c.requested_version) + '</code>' : '-';
          const safeRes = c.resolved_version ? '<code>' + escapeHtml(c.resolved_version) + '</code>' : '-';
          const safeScore = c.score_crates_version ? '<code>' + escapeHtml(c.score_crates_version) + '</code>' : '-';
          const safeManifests = c.manifest_paths.map(m => '<code>' + escapeHtml(m) + '</code>').join(', ');
          return `
            <tr>
              <td><strong>${{safeCrate}}</strong></td>
              <td><span class="badge ${{badgeClass}}">${{escapeHtml(c.status)}}</span></td>
              <td>${{safeReq}}</td>
              <td>${{safeRes}}</td>
              <td>${{safeScore}}</td>
              <td>${{safeManifests}}</td>
            </tr>
          `;
        }}).join('');

        const safeRepo = escapeHtml(repo.repo_name);
        const safePaths = repo.project_paths.map(p => '<code>' + escapeHtml(p) + '</code>').join(', ');

        card.innerHTML = `
          <div class="repo-header">
            <span class="repo-name">${{safeRepo}}</span>
            <div>
              <span class="badge badge-managed">${{repo.managed_crates_count}} managed</span>
              <span class="badge badge-mismatch">${{repo.mismatch_crates_count}} mismatch</span>
              <span class="badge badge-unmanaged">${{repo.unmanaged_crates_count}} unmanaged</span>
            </div>
          </div>
          <div class="paths-list">Project paths: ${{safePaths}}</div>
          <table>
            <thead>
              <tr>
                <th>Crate</th>
                <th>Status</th>
                <th>Requested</th>
                <th>Resolved</th>
                <th>${{escapeHtml('{ref_short_escaped}')}}</th>
                <th>Manifest</th>
              </tr>
            </thead>
            <tbody>
              ${{cratesRows}}
            </tbody>
          </table>
        `;
        container.appendChild(card);
      }}
    }}

    filterData();
  </script>
</body>
</html>
"""
