# Evaluation suite

This folder holds the tools that decide whether an audit run counts as evidence that Evidence-to-Proof Review works. It is separate from `validation/`, which records the audits that were executed.

| Part | File | What it does |
| --- | --- | --- |
| Scorer | `scorecard.py` | Scores finished audits against answer keys, with baselines and a gate. |
| Blindness | `blindness.py` | Keeps answer keys away from the audited agent and looks for leaks afterwards. |
| Case generator | `generate.py` | Writes cases whose answer keys are computed by a program. |
| Blind runner | `run_blind.py` | Runs each contestant on staged cases through ALA, with only the case mounted. |
| Comparison | `compare.py` | Compares two contestants on the same claims. |

None of these calls a model except `run_blind.py run`. No blind campaign has been run yet: the sections on the generator, the runner and the comparison describe tools that are tested with a stand-in agent, not results. What is still missing is listed under [Not built yet](#not-built-yet).

## What the scorer adds

`validation/evaluate.py` compares one field, `semantic_state`, with the answer keys and reports the number of matches. `scorecard.py` keeps that comparison, claim for claim, and adds the context needed to read it.

- **Baselines.** What a constant answer scores on the same claims. A report with no injected problem expects `supported` everywhere, so agreement on it shows the absence of false alarms and nothing else. The scorer therefore reports reports with and without injected problems separately.
- **Error directions.** `false_support` (a claim that should not be accepted is marked supported), `false_refutation`, `error_detection` and `withheld_support`, each with a 95% Wilson interval.
- **Echo.** Where a key row carries `echo_state`, the verdict of a reader who believes what the packet says about itself, the scorer counts the answers that give that verdict, separately for definite verdicts and for `undetermined`, with a control for the second. A claim counts when any repeat gives the verdict. Keys without the field have no such claims, and the rates are then empty. See [What the packet says about itself](#what-the-packet-says-about-itself).
- **False formal certainty.** Claims whose verdict disagrees with the key although a finding carries `proved` or `refuted`. This is the failure the product must not have, because a checked theorem then stands behind a wrong answer.
- **Evidence.** Recall of the source anchors the key lists, after resolving extraction anchors such as `docx-p-0006` to the visible markers such as `[P02]` that label them. Brief and report anchors in a key are dropped, because the audit contract does not accept them as source evidence.
- **Repeats.** A claim audited several times keeps one record. It counts as correct only when every repeat is correct, and as a false support when any repeat says supported. Runs whose findings are byte-identical count as one audit, and a report with no run counts as zero.
- **Coverage.** Reports with no run, listed runs that produced no findings, claims without a finding, findings without a key, and duplicated runs are all listed and all fail the coverage check.
- **Blindness.** Whether the answer keys and the run labels were readable from the folders declared as visible to the audited agent, and whether key wording shows up in the run.
- **Gate.** Thresholds read from a file written before the runs. The exit status is 1 unless every check passes. A threshold name the gate does not know, or a value of the wrong type, stops the scorer, so a misspelling cannot drop a check.

The argument audit (`argument_status`) is not scored, because the current keys carry no expected value for it.

## Score the retained runs

```sh
python3 evalsuite/scorecard.py --keys examples/evaluator --agent-root . \
  --gate evalsuite/thresholds.json --output tmp/evalsuite/scorecard-legacy.json
```

`--agent-root .` states that the audited agents could read the whole repository, which is how the retained runs were made. The command exits with status 1: agreement is complete, but the gate refuses to treat it as proof. The reasons are in the `gate` section of the output.

Reading on the twenty retained runs, 10 November 2026:

| Measure | Result |
| --- | --- |
| Agreement, all claims | 237 of 237 |
| Best constant answer, all claims | 60.8% (always `supported`) |
| Agreement, reports with injected problems | 124 of 124, against 46.0% for the best constant answer |
| Agreement, reports without injected problems | 113 of 113, against 100% for always `supported` |
| Injected errors detected | 62 of 62, interval 94.2% to 100% |
| False support | 0 of 93, interval 0% to 4.0% |
| False refutation | 0 of 144, interval 0% to 2.6% |
| False formal certainty | 0 |
| Expected source anchors cited | 617 of 619 |
| Blindness | exposed: the keys lie inside the declared agent root |
| Repeats per report | 1 |
| Runs with key wording in authored files | 3 |

The three flagged runs share eight or nine consecutive words with a key rationale, wording that appears nowhere in the supplied report or sources. Each needs a human reading; the scorer lists the phrases under `per_run[].contamination`. Shared wording can come from reading the key, from one model family writing both texts, or from chance.

## Blindness

The audited agent must not be able to read the answer keys, the other report variant, authoring masters or earlier audits of the same report. An instruction in a prompt is not a boundary. `blindness.py` treats the boundary as data.

- `exposure` reports whether a protected path lies inside, or contains, a folder declared as readable by the agent. Paths are compared after resolving symbolic links and then by file identity, so the same folder written in another letter case still counts. A declared root that is not an existing directory gives `not_attested`.
- `seal` copies answer keys to a new folder outside every declared agent root and embeds one canary string in all of them.
- `stage` copies one report and its sources into a new folder named `run-<random>`. The report is always `case/report.md`, so neither the folder nor the report file name reveals the dossier or the variant. The label goes to a records folder outside the arena, together with the hashes of the staged files. Each staging of the same report gets the next audit number unless `--repeat` names one, and a number already used is refused. Staging refuses symbolic links, a records folder inside the arena, a withheld path that does not exist, and any staged file, including the copied skill, that holds a canary or the bytes of withheld material.
- `scan_run`, used by the scorer, searches every file of a finished run for the canary, and every text file for eight consecutive words of a key rationale that appear in agent-authored files and nowhere in the supplied inputs. With the stage record, a file counts as supplied only when its hash was staged. Without it the scan goes by folder names, which an agent could imitate, so the gate accepts only scans made against stage records.

```sh
mkdir -p /arena
python3 evalsuite/blindness.py seal --source /keys/draft --dest /keys/sealed --agent-root /arena
python3 evalsuite/blindness.py stage --report fixture/report.md --sources fixture/sources \
  --arena /arena --records /keys/records --dossier 11-example --variant mixed \
  --withheld /keys/sealed --withheld fixture/report-correct.md --skill skills/evidence-to-proof-review
python3 evalsuite/scorecard.py --runs /exported-bundles --keys /keys/sealed \
  --records /keys/records --agent-root /arena --gate evalsuite/thresholds.json --output scorecard.json
```

The exported bundle of each run must sit in a folder named after its `run-<random>` id, so the scorer can match it with its stage record.

Limits that the tool cannot remove:

- The agent roots are declared by whoever runs the scorer. The tool does not observe what a sandbox mounted. The status is therefore `isolated_as_attested`, never "verified".
- Folders are compared by location. A hard link to a key file placed inside an agent root is not detected.
- File names under `sources` are staged unchanged. A fixture must not name a source after the variant.
- The pilot launcher mounts the whole repository read-only for the agent (`tools/ala.mjs`). A run made that way is exposed to anything stored in the repository, including `examples/evaluator/` and `validation/runs/`. A blind run goes through `run_blind.py`, which mounts only the staged folder, a private home and the Lean toolchain. Pilot runs also share the agent's state (session history) with earlier runs and with your own sessions, so a pilot run is not blind against them.
- A clean scan shows that no key wording was copied. It does not show that the keys were never read.
- Runs that were audited with the keys readable cannot be made blind afterwards.

## Cases with computed answer keys

The retained answer keys are opinions of the fixture authors, written together with the errors. `generate.py` builds cases where no opinion is involved.

A case starts as structured facts. Some facts are deliberately left out of the rendered sources and become unknowns with a finite set of possible values: an approval sheet that is not in the packet, a reply date that was not recorded, an illegible signature. A claim is a statement that is true or false once every unknown has a value. Its verdict is computed by trying every combination the sources allow:

| The claim is | Verdict |
| --- | --- |
| true in every combination | `supported` |
| false in every combination | `refuted` |
| true in some and false in others | `undetermined` |

The correct report holds only supported claims. The mixed report replaces some of them with typed errors, such as a total that uses a superseded amount, a deadline computed under the wrong rule version, or a statement about a document that was not supplied. The same computation decides what each error deserves, and a replacement that happens to stay true is dropped.

```sh
python3 evalsuite/generate.py --fixtures /campaign/fixtures --keys /campaign/keys-draft --seed 7 --dossiers 30
```

Four case families exist: a sampled payment ledger (sums, counts, bounds), reply deadlines under two rule versions (dates, rule choice, extensions), an approval policy (rule entailment) and a calibration packet with a transmittal sheet (what the packet says it holds against what it holds). Each key row carries the claim kind, the error type and the derivation: how many combinations were tried, in how many the claim is true, and the range of values the sources allow.

Nothing but the sources tells a right claim from a wrong one:

- Both reports of a dossier use the same claim identifiers in the same order.
- Every wording occurs both as a right claim and as a wrong or unestablished one. An exact count is right when the sources settle it and unestablished when a sheet is missing. A claim about the rule version or the approval tier is made about a case on either side of the boundary. A statement about an annex is right or wrong depending on whether the annex was supplied and what it says. A test fails if any wording, alone or with its citations, is one-sided over several hundred cases.
- Gaps are not always present. A missing approval sheet, an unrecorded reply, an undecided extension and an illegible signature are each absent from four or five cases in ten, so no gap can be assumed.

The tests derive every verdict a second time, with separately written code that reads only the rendered Markdown. On 80 generated cases, twenty of each family, the two derivations agree on every claim.

### What the packet says about itself

In the first three families a gap is only ever described: a table cell reads "Approval sheet not in packet", and no file exists that could show otherwise. A reviewer who repeats what a packet says about its own contents is therefore never wrong on them. The `packet` family separates the two.

- Each unit has one calibration certificate, an annex that is a file of its own, `annex-a.md` and so on. A file that was not supplied does not exist.
- `transmittal.md` states for every annex whether it is included.
- In about seven cases in ten the sheet is wrong about one or two annexes: it lists a supplied certificate as not included, or lists as included a certificate that is not there. In the other cases it is right about every annex, so it can be neither trusted nor dismissed unread.
- `method.md` defines "included in the supplied packet" as being among the source documents supplied for the review. It says that only a certificate establishes a result and a test date, that a certificate which was not supplied establishes neither, and how the days before the review date are counted.
- A report makes one statement for each of the five to seven units, about its annex or about the unit, and one about how many units may stay in service. Which of the two a unit gets is drawn without regard to what was supplied, and no two claims concern the same unit. The claims about single units therefore do not show which files exist. The claim that counts units can still disagree with them; see the limits below.
- A report cites a certificate whether or not its file was supplied. The key lists only passages that exist.

The key is computed from the files, as for every family. The generator also computes each claim a second time in the world of a reader who takes the sheet at its word: an annex is included when the sheet says so, and a certificate is read only when its file is there and the sheet lists it as included. Where that verdict differs from the key, the key row records it as `echo_state`, with the counts behind it in `echo_derivation`.

| The sheet says | The files show | A reader who believes the sheet |
| --- | --- | --- |
| Not included | The certificate is supplied | answers `undetermined` on claims the certificate settles, rejects "Annex B is included" and accepts "Annex B is not included" |
| Included | No such file | accepts "Annex C is included" and rejects "Annex C is not included" |

**Reading the echo rates.** An answer that equals the echo verdict is what a reader who believes the sheet would give. It does not show that the reviewer believed the sheet. The scorer therefore reports two rates and a control, under `semantic.*`:

| Measure | Claims counted in | An answer counts when it is |
| --- | --- | --- |
| `echo_asserted` | Claims whose echo verdict is a definite one. In this family, the statements that an annex is or is not included. | the echo verdict. Little else explains accepting "Annex B is not included" while `annex-b.md` lies in the folder. |
| `echo_withheld` | Claims whose echo verdict is `undetermined`: what a supplied certificate settles, where the sheet lists it as not included. | `undetermined`. A reviewer who is merely cautious gives the same answer without reading the sheet. |
| `withheld_without_conflict` | The control: claims of the same kinds that the sources settle and whose verdict the sheet does not change. | `undetermined`. |

Only an excess of `echo_withheld` over the control can be put down to the sheet. A reviewer who answers `undetermined` to everything reaches 100% on both. The two rates pool three kinds of claim in different proportions: the claim that counts units is about half of the withheld claims and a third of the control. A reviewer who is more cautious on one kind therefore shows a small difference with no echo behind it, so compare the two kind by kind, in `echo_by_kind`. `echo` is the first two measures taken together and should not be read on its own: a little over half of its claims are of the withheld kind. Each rate is a share of the claims that could have been answered that way, not a share of the wrong answers. Every echo is also a wrong answer and already counts in the accuracy and in the error directions. Keys that do not name a `claim_kind` have no control.

`compare.py` prints the three for each arm as "Packet's word taken over its files", "Withheld where its word is wrong" and "Withheld where its word is right", with an unanswered claim counted in each.

**How many such claims.** The generation summary gives the number as `echo_claims`. One case in five that holds a conflict has no such claim, because the statement drawn for the unit concerned is one the sheet does not change. A certificate listed as included but missing can echo only through the statement about its annex, which a unit gets 45 times in 100. Twelve dossiers of all four families hold between 0 and 14 such claims, 6 at the median, and none for about one seed in eleven (300 seeds). Twelve packet dossiers hold 10 to 38, 22 at the median (100 seeds). A campaign that is to measure these rates should generate with `--families packet` and check the number first.

Limits of this family:

- Only presence is varied. A copy described as unreadable that is in fact clean needs scanned fixtures, which the generator does not write.
- A certificate listed as included but missing gives an echo verdict only for the statements about whether it is included. A reviewer who invents its contents is counted as a false support, not as an echo.
- The claim that counts units can disagree with the claims about single units. "Exactly 4 of the 6 units may stay in service" can be established only if every certificate was supplied, so beside "Annex D is not included" one of the two is not established. No correct report holds such a pair. Of 400 altered reports 143 do, which marks them as altered without a look at the sources. In 46 of them the counting claim is the only one whose removal settles the disagreement, and it is the wrong one in 42. No claim about a single unit is ever singled out that way, so the statements about what is included are not given away. Removing this would mean drawing the errors of a report together instead of one by one.
- A report can state truthfully what a certificate records only if the certificate was supplied. A statement about a certificate is therefore more often about a supplied one than not. In altered reports it also stands in for a missing one, so it cannot be accepted unread.
- An agent that lists the folder before it reads the sheet may make no such error. If every contestant is at zero, the family does not separate them.
- In a small campaign many resamples of dossiers hold no conflict, and the comparison then prints these differences without an interval.
- The audit hands in one sheet, so the suite cannot show a reviewer that admits the error when challenged and repeats it on the next question.

Limits:

- The key is exact for the structured facts. It is right for the documents only if the rule sentences in the sources mean what the generator computes. The second derivation reads those sentences the same way, so it does not test this. The sentences are a fixed, small set that a person can review once, unlike hand-written fixtures.
- Unestablished claims are the smallest group: roughly one altered claim in seven. A measure that rests on them needs more dossiers than the others.
- The cases are synthetic and come from four templates. Dossiers of one family resemble each other, so a result on them says little about other kinds of documents.
- Only `supported`, `refuted` and `undetermined` are generated. No case has `both` or `not_applicable` claims.
- A capable agent may judge these cases almost perfectly with or without the skill. If every contestant is near 100%, the cases cannot separate them, and harder families are needed before any conclusion.

## A blind campaign with three contestants

`run_blind.py` runs the same agent, model and time limit in three ways, defined in `arms.json`:

| Arm | Skill staged | Lean mounted | Shows |
| --- | --- | --- | --- |
| `plain` | No | No | What the agent does on its own. |
| `skill-no-lean` | Yes | No | What the method adds. |
| `skill-lean` | Yes | Yes | What Lean adds on top of the method. |

Every arm receives the same task text and the same definitions of the five verdicts. Only the section on how to work differs (`prompts/`). Every arm hands in the same answer sheet, `verdicts.json`, and only that sheet is scored. The runner turns it into the bundle layout the scorer reads, using the original report and sources, never the agent's copies.

```sh
python3 evalsuite/blindness.py seal --source /campaign/keys-draft --dest /campaign/sealed --agent-root /campaign/arena
python3 evalsuite/run_blind.py plan  --fixtures /campaign/fixtures --keys /campaign/sealed --arena /campaign/arena \
  --out /campaign/out --agent codex --model MODEL --minutes 45 --repeats 3 \
  --lean /toolchains/lean-4.24.0-linux/bin/lean --agent-home /campaign/agent-home
python3 evalsuite/run_blind.py probe …same arguments…
python3 evalsuite/run_blind.py run   …same arguments…
```

`plan` shows what would run and starts nothing. `probe` and `run` need Linux with Bubblewrap or macOS with `sandbox-exec`, and ALA: `--ala`, else `ALA_CLI`, else an `AdvancedLanguageAgent` checkout beside or inside this repository, else the copy `npm install` puts under `node_modules`. For that copy the recorded commit is the one npm wrote to `node_modules/.package-lock.json` when it installed it; any other copy without a Git folder of its own records no commit. Only `run` reaches a model.

What the agent can read during a review:

- **Its run folder**, `arena/run-<random>`: one report as `case/report.md`, the sources, the task text and, for the skill arms, a copy of the skill. It is the only writable folder.
- **A private home**, copied from `--agent-home` for this review alone. The folder given as `--agent-home` must hold only a login made for the campaign, for example after `CODEX_HOME=/campaign/agent-home/.codex codex login`. Nothing a review writes there goes back: the readable record (session history, notes, logs) is kept with the review, the copied login is removed, and the runtime files and caches that the agent's client unpacks there are dropped. Without a private home, ALA mounts the user's own agent state, and a review could read the session history of an earlier one, including the other report of the same dossier. The runner refuses that unless `--shared-agent-state` is passed, and the comparison then calls the result exploratory.
- **The Lean toolchain**, read-only, for the `skill-lean` arm only. It must sit in a folder of its own.
- **The Python toolchain**, read-only, for every arm, only where the interpreter in use is the one that `node tools/setup-toolchain.mjs` installed: its folder under `tmp/toolchains`, which the same blindness checks cover. Each arm's task text then ends with one sentence that names that interpreter. With a system interpreter nothing is mounted or added.
- **System folders and programs**, read-only: on Linux ALA mounts `/usr`, `/etc`, `/bin`, `/lib`; on macOS its Seatbelt profile allows reading the system and software folders listed in ALA's DS006. On both, the folder of the Node.js installation that runs ALA and the folder that holds the coding agent's executable.

The repository, the answer keys, the fixtures, the records and the other reviews are not mounted. The runner itself never opens an answer key: it reads the manifest that sealing wrote, for the canary and the hashes of the sealed files.

**The probe.** Before the first review, `run` starts ALA once per arm with a shell script in place of the coding agent. Before it starts ALA, it asks ALA's own discovery, with the environment ALA will get, which program it would run, and it refuses to run when that is anything other than its simulated agent. Inside the real sandbox the script checks that the keys, the records, the fixtures, another review's folder and the repository's own keys are not visible, searches the run folder, the home, `/tmp`, `$TMPDIR` and the toolchains for the key canary, and checks that Lean and the skill are present exactly for the arms that should have them. On Linux it also records its mount namespace: results from the runner's own namespace would mean the script looked at the host, and the probe fails. macOS has no mount namespace, so there the runner decides from three acts that must each be refused inside and work on the host: a nested `sandbox-exec`, reading a file the runner put in the campaign's `probe` folder, and `kill -0` to the runner; and the environment of an outside helper process must be unreadable inside. A failed probe starts no review. On 11 November 2026 the probe passed for all three arms on Ubuntu 26.04 with Bubblewrap 0.11.1 and ALA commit `bcc441a`, with a stand-in for Lean. It failed, as it should, when a copy of the keys was placed inside the mounted toolchain and when key material was placed in the agent home.

**What counts.** A review that has started is final and is never run again.

| How the review ended | Valid sheet | Recorded as |
| --- | --- | --- |
| The agent ended, or the time ran out | Yes | `scored`. The comparison also gives a strict reading in which a review that did not end on its own counts as failed. |
| Any | No | `failed`. Every claim of that report counts as wrong. |
| The runner was stopped during the review | Not read | `failed`, closed at the next invocation. |

The campaign folder records the agent, model, effort, time limit, repeats, seed, arms, case hashes, skill hash, login, Lean, ALA and the code. An invocation with other settings is refused.

An arm that ends three times in a row with a non-zero exit and no sheet stops the campaign, because that pattern is an outage, not a weak reviewer. The stop is kept in `stopped.json`, further `run` calls on that folder are refused, and the comparison calls any reading of it exploratory. Those reviews stay failed, and a new campaign needs a new folder.

What an agent leaves behind is handled as hostile. Links are never followed, permissions it took away are restored before anything is moved, and the bundle is built from the original report and sources.

Limits:

- `formal_status` on the sheet is what the agent reports. The runner does not rerun Lean. An arm without Lean cannot report a proof: the runner records such a claim as not attempted and counts it. For the Lean arm, a wrong verdict reported with `proved` or `refuted` counts as false formal certainty, but a right verdict reported as proved is not evidence that a proof exists.
- The skill arms follow the Markdown-native workflow, because the pilot tools live outside the skill folder and are not staged. The pilot profile is not evaluated.
- If the agent's client rewrites its login during a review, the change is recorded and discarded. Should that have been a real token refresh, later reviews may fail to sign in, and the campaign stops as described above. A fresh login made just before the campaign, or an API key in the environment, avoids this.
- The sandbox has network access, which the model needs. The task forbids web search; nothing enforces that.
- The probe uses a simulated Codex. It shows what ALA mounts for these arguments, not what a particular coding agent does with them. It does not search the system folders or the folder of the real agent program; at ALA commit `bcc441a` that folder is the `bin` folder of the Codex release, not the user's `~/.codex`.
- Two reviews of one report that hand in byte-identical sheets are flagged by the scorer as a copied run, which fails its gate. That errs on the safe side.

## Compare the contestants

```sh
python3 evalsuite/compare.py --campaign /campaign/out --keys /campaign/sealed --ladder plain,skill-no-lean,skill-lean
```

`--ladder` compares each arm with the next and the first with the last. A claim counts as right only when every review of its report judged it right. The main measure is the share of claims judged right. The others are false claims caught, unestablished claims recognised, wrong claims accepted or unanswered, true claims rejected or unanswered, and the two echo rates with their control, each with an unanswered claim counted in it.

A claim that got no answer in some review counts against its arm on every measure, whether the review handed in no sheet or the sheet left the claim out. Silence is not a careful answer: without this rule an arm whose reviews fail would show no wrong claim accepted. The number of unanswered claims is printed for each arm.

The difference between two arms comes with a 95% interval from resampling whole dossiers, because claims of one dossier share its documents. An exact sign test over dossiers and an exact McNemar test over claims are printed as well. The comparison is called exploratory, and must not be presented as a campaign result, when:

- fewer than ten dossiers were compared;
- reviews exist for only one arm, or the campaign has fewer repeats than it planned;
- reviews of an arm ended with a crash and no sheet, which looks like an outage;
- agent state was shared, or reviews started without a passed probe.

Score each arm against the gate separately; `run` prints the exact `scorecard.py` commands, with every folder the agent could read as an `--agent-root`.

## Thresholds

`thresholds.json` holds provisional values. Change them before a campaign, not after seeing its results.

| Check | Value | Reason |
| --- | --- | --- |
| `require_isolated_keys` | true | Agreement with readable keys proves nothing. |
| `require_no_contamination_flags` | true | Every run is scanned against its stage record, and none shows a canary or copied key wording. |
| `require_full_coverage` | true | No missing report, run or claim, no finding without a key, no duplicated run. |
| `min_repeats_per_report` | 3 | One run cannot show stability. Counts separate audits of every report in the keys. |
| `max_false_formal_certainty` | 0 | A checked theorem behind a wrong verdict is the worst failure. |
| `max_false_support_upper_bound` | 0.05 | Upper end of the interval, so a small suite cannot pass by luck. |
| `max_false_refutation_upper_bound` | 0.05 | Same, for false alarms. |
| `min_error_detection_lower_bound` | 0.8 | Lower end of the interval for injected errors found. |
| `min_lift_over_best_constant_on_flawed_reports` | 0.3 | Lower end of the accuracy interval minus the best constant answer. |

The intervals treat claims as independent. Claims share dossiers, so the real uncertainty is wider; count dossiers as well as claims when sizing a campaign.

The echo rates have no threshold. Every echo already counts as a wrong answer under the checks above. A limit on a rate itself would be a new check in `scorecard.py`, to be set before a campaign like the others.

## Tests

```sh
python3 -m unittest tests.test_evalsuite_scorecard tests.test_evalsuite_blindness \
  tests.test_evalsuite_generate tests.test_evalsuite_run_blind
```

The scorer and blindness tests check claim-for-claim agreement with `validation/evaluate.py` on the retained runs, every error direction on constructed bundles, the gate, and the refusals of the blindness module. Each way the gate could pass wrongly that review found has a test: a skipped scan, a crashed run, a copied run, a misspelled threshold or root, and a folder named in another letter case.

The generator tests derive every computed key a second time from the rendered documents and check that no wording is one-sided. For the packet family they also derive every echo verdict a second time, from the sheet and the files, and check that both kinds of conflict occur and that the sheet is often right. A further test reads only the reports of 400 packet cases: no claim may be always right or always wrong given what else its report says about the same unit, no report may call an annex missing while stating what its certificate records, and the number of claims must not give away how many certificates were supplied. Another checks, again from the reports alone, whether all claims of a report could be established together: every correct report must pass, and the share of altered reports that fail, with the counting claim as the only one ever singled out, must stay within the limit stated above. The scorer and comparison tests count echoes and the control on constructed sheets and on a campaign in which one stand-in agent believes the sheet. The runner tests run whole campaigns with a stand-in agent in place of ALA and a model: what each arm is given, a missing or hostile answer sheet, links and locked folders left behind, an agent that edits its copies, an interrupted review, a stopped campaign, changed settings, layouts that put answers within reach, unreadable keys, and the private home. The probe script is also executed outside a sandbox, where it must report what it sees. None of the tests needs Lean, ALA or a model. The probe itself needs the real sandbox and is run with `run_blind.py probe`.

## Not built yet

- **A campaign.** The generator, the runner and the comparison exist, but no review has been run with a model. Start with a few dossiers to learn how long each arm needs, then fix the time limit and the thresholds before the real campaign.
- **A second check of reported proofs.** The runner should rerun Lean on what the `skill-lean` arm hands in, inside a sandbox, before a reported proof counts as one.
- **Harder and more varied cases.** More families, `both` and `not_applicable` claims, and a set built from real public documents with human labels.
- **More ways for a packet to be wrong about itself.** The same case staged several times with only the sheet's wording growing stronger, where any change in the verdicts is the effect; a source that describes its own contents wrongly, such as a stated total that its rows do not give; and scans described as unreadable that are clean.
- **Fidelity mutants.** Lean models changed so that the theorem still checks but no longer matches the source. The mechanical gates cannot catch these; the measure is how often semantic review does.
- **Readable-document bundles.** The scorer reads the legacy export layout. The current pilot profile has no answer keys and needs its own reader.
- **Expected argument status** in the keys, so the argument audit can be scored.
