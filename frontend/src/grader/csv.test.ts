// Tests for the browser-side steps that prepare Chen's files: cleaning answers
// exported as HTML, recognising a file, and splitting an explanations export. The
// fixtures are made-up files with the same columns and markup as his.
import { describe, expect, it } from "vitest";

import explanations from "../../../backend/tests/fixtures/chen_like/explanations.csv?raw";
import rubricFile from "../../../backend/tests/fixtures/chen_like/rubric.yaml?raw";
import { choose, htmlToText, looksLikeHtml, NO_FILE, parseTable, planFor, prepareExplanations, recognize } from "./csv";

describe("htmlToText", () => {
  it("leaves plain text exactly as it is, even with < and > in it", () => {
    for (const text of ["f < mg sin(theta), so it speeds up", "if a<b and b>c then a<c", "x > 0 <3", "plain"])
      expect([looksLikeHtml(text), htmlToText(text)]).toEqual([false, text]);
  });

  it("turns Canvas markup into text the grader can read", () => {
    const html =
      '<p>F<sub>net</sub> = m a<sup>2</sup>&nbsp;and v<sub>max</sub></p><p>with&nbsp;<img class="equation_image" ' +
      'alt="LaTeX: f_k=\\mu_kN" data-equation-content="f_k=\\mu_kN"></p><p>' +
      '<img alt="LaTeX: a&gt;0" src="x">, <img alt="a sketch">, <img src="y"></p>' +
      '<ol><li><span style="color: red">first</span></li><li><strong>second</strong></li></ol>' +
      "<ul><li>a point</li></ul><p>one<br>two &amp; &lt;three&gt;</p><script>alert(1)</script>";
    expect(htmlToText(html)).toBe(
      "F_{net} = m a^2 and v_{max}\n\nwith \\(f_k=\\mu_kN\\)\n\n\\(a>0\\), [image: a sketch], [image]\n\n" +
        "1. first\n\n2. second\n\n- a point\n\none\ntwo & <three>",
    );
  });
});

describe("recognize", () => {
  it("names each kind of file before anything is loaded", () => {
    expect(recognize(explanations)).toEqual({
      kind: "explanations",
      sentence: "An explanations export: 4 answers to 2 versions of one problem.",
    });
    expect(recognize(rubricFile)).toEqual({ kind: "rubric", sentence: "A rubric file with 3 items." });
    const bank = "bank_info:\n  title: Radial forces\nquestions:\n  - multiple_choice:\n      id: q1\n      answers:\n        - answer:\n" +
      "            text: x\n  - multiple_choice:\n      id: q2\n";
    expect(recognize(bank).kind).toBe("bank");
    expect(recognize(bank).sentence).toContain("with 2 questions");
    expect(recognize("student_id,problem_id,response_text\ns1,v1,An answer\n").kind).toBe("responses");
    expect(recognize("problem_id,problem_text,rubric_item\nv1,Why?,Item a\n").kind).toBe("problem_info");
    expect(recognize("problem_id,text\nv1,Item a\n").kind).toBe("problem_info"); // "text" is not taken for answers
    expect(recognize("  \n").kind).toBe("empty");
  });
});

describe("planFor", () => {
  const file = (name: string, text: string) => choose({ ...NO_FILE, name, text });
  const info = file("info.csv", "problem_id,problem_text\nv1,Why?\n");
  const answers = file("answers.csv", "student_id,problem_id,response_text\ns1,v1,Because\n");
  const export_ = file("explanations.csv", explanations);
  const rubric = file("rubric.yaml", rubricFile);
  const bank = file("bank.yaml", "bank_info:\n  title: x\nquestions:\n  - essay:\n      id: q1\n");

  it("loads an explanations export with a rubric file, and leaves out what it cannot read yet", () => {
    const plan = planFor([export_, rubric, bank, answers]);
    expect([plan.ready, plan.problems?.name, plan.rubric?.name, plan.answers]).toEqual([
      true,
      "explanations.csv",
      "rubric.yaml",
      null,
    ]);
    expect(plan.say).toContain("The rubric file becomes the rubric every version shares.");
    expect(plan.unused).toEqual([
      "bank.yaml is not loaded.",
      "answers.csv is not loaded. The explanations export already holds the answers.",
    ]);
  });

  it("holds a responses file given with a Problem Info CSV", () => {
    const plan = planFor([info, answers]);
    expect([plan.ready, plan.problems?.name, plan.answers?.name, plan.rubric]).toEqual([true, "info.csv", "answers.csv", null]);
  });

  it("says what is missing instead of loading the wrong thing", () => {
    expect(planFor([]).say).toBe("");
    for (const [files, say] of [
      [[rubric], "Add the file with the problems too: a Problem Info CSV or an explanations export."],
      [[answers], "Add the file with the problems too: a Problem Info CSV or an explanations export."],
      [[bank], "Nothing here has the problems. Choose a Problem Info CSV or an explanations export."],
      [[info, export_], "Choose one file with the problems: info.csv and explanations.csv each have them."],
      [[info, rubric, file("other.yaml", rubricFile)], "Choose one rubric file: rubric.yaml and other.yaml are each a rubric."],
    ] as const) {
      const plan = planFor([...files]);
      expect([plan.ready, plan.say]).toEqual([false, say]);
    }
  });
});

describe("prepareExplanations", () => {
  const prepared = prepareExplanations(explanations, ["Jane Doe"], true);

  it("makes one version per ItemID, with its problem text", () => {
    const versions = parseTable(prepared.versionsCsv).rows;
    expect(versions.map((r) => r[0])).toEqual(["problem_id", "900101", "900102"]);
    expect(versions[1][1]).toContain("inclined at 28.40∘");
    expect(versions[2][1]).toContain("Find the acceleration of the sled in m/s².");
    expect(prepared.versions).toBe(2);
  });

  it("cleans each answer, then strips names and swaps ids, and keeps EarnedPoints in the browser", () => {
    const rows = parseTable(prepared.csv).rows;
    expect(rows[0]).toEqual(["student_id", "problem_id", "response_text"]);
    expect(rows.slice(1).map((r) => [r[0], r[1]])).toEqual([
      ["S001", "900101"],
      ["S002", "900101"],
      ["S003", "900102"],
      ["S004", "900102"],
    ]);
    expect(rows[1][2]).toBe(
      "I used Newton's second law along the ramp.\n\nF_{net} = mg sin(θ) - f_k = ma\n\nwith \\(f_k=\\mu_kN\\)",
    );
    expect(rows[2][2]).toBe("Friction is less than gravity's pull, so f < mg sin(theta) and it speeds up.");
    expect(rows[3][2]).toBe(
      "1. Forces: weight, normal force and friction.\n\n2. N = mg cos(θ)\n\n3. a = g(sinθ - μcosθ)\n\nSigned, [STUDENT]",
    );
    expect(rows[4][2]).toBe("x^2 grows & the sled speeds up\nbecause \\(a>0\\)");
    expect(prepared.csv).not.toContain("EarnedPoints");
    expect(prepared.csv).not.toMatch(/x7Kp2Q|Jane|Doe/);
    expect(prepared.idMap).toEqual({ S001: "x7Kp2Q", S002: "b3Nw9L", S003: "Qe5Rt1", S004: "mT4vZ8" });
    expect(prepared.notes).toEqual(
      expect.arrayContaining([
        "Each of the 2 ItemIDs is a version, with the problem text the export gives for it.",
        "Answers written as HTML were turned into plain text, keeping 2 equations as LaTeX.",
        'Not uploaded, and never left your browser: "EarnedPoints".',
      ]),
    );
  });
});
