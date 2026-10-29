# Maintaining the public report

Edit `report.md` and rebuild the reading copy from the repository root:

```text
npm --prefix tools/report install
npm --prefix tools/report run build
git add docs/report tools/report
git commit -m "Revise technical report"
git push origin main
```

GitHub Pages serves `main:/docs`; every subsequent push republishes changes. The report URL is https://nxsyy55.github.io/simple-expert-modeling-loop/report/. Keep the working-draft designation until author review is complete. A Chinese edition will be added after the English revision.

Commit the article, generated HTML, figures and compact numerical summaries. Do not copy historical runtime records, full internal reports, source collections or private correspondence into the public repository without separate disclosure approval. The local archive is not a public deployment source.

The report concerns evolving historical implementations, not a newly validated run of the extracted public framework at commit `262948d`. Original project documentation follows the repository license; linked third-party works and quotations retain their authors' rights.
