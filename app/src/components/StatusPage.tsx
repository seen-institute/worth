type State = "built" | "assumed" | "missing" | "not ours";

interface Item {
  what: string;
  state: State;
  detail: string;
  who?: string;
}

const LABEL: Record<State, string> = {
  built: "Built",
  assumed: "Assumed",
  missing: "Missing",
  "not ours": "Not ours",
};

/**
 * Progress against the methodology's own starting point. "Two things are
 * defensible from day one. The empirical reference distribution... And Method 0,
 * the slope... Both are available from the first validated extract."
 *
 * Everything an engineer can finish alone is finished. What is left is a partner
 * file and four things that need somebody who is not an engineer.
 */
const ITEMS: { group: string; items: Item[] }[] = [
  {
    group: "The two things defensible from day one",
    items: [
      {
        what: "A complexity score per case",
        state: "built",
        detail:
          "Eleven markers. Eight come from hospital fields and three are read out of the operative note. Every value points back to the file, row and column it came from.",
      },
      {
        what: "The reference distribution, per code",
        state: "built",
        detail:
          "What the work actually looks like across a code. Median, spread, range.",
      },
      {
        what: "Method 0, with a confidence interval",
        state: "built",
        detail:
          "One code, one payer at a time. Needs no comparison to other specialties.",
      },
      {
        what: "Linkage rate reported alongside",
        state: "built",
        detail:
          "Published with every value. On made-up data it is 100%. On real data it will not be. The cases that fail to match will be the complicated ones.",
      },
    ],
  },
  {
    group: "Before any of it can be published",
    items: [
      {
        what: "Weights set by clinical and statistical review",
        state: "missing",
        who: "A clinician",
        detail:
          "The weights are my placeholder (with the help of Claude). Nothing is publishable while they stay that way.",
      },
      {
        what: "A real extract from a partner",
        state: "missing",
        who: "Mount Sinai data owner",
        detail:
          "Everything here runs on the aforementioned fake dataset. The first ingestion of real data is the first honest test.",
      },
    ],
  },
  {
    group: "Assumed until a partner file arrives",
    items: [
      {
        what: "What the files look like",
        state: "assumed",
        who: "An analyst at Mount Sinai",
        detail:
          "Column names, the delimited format, and the number joining a case to its payment are all invented.",
      },
      {
        what: "What the operative notes look like",
        state: "assumed",
        who: "An analyst at Mount Sinai",
        detail:
          "Ours come from one template. Real notes vary by surgeon. Where a note has no headings the reader finds nothing and says nothing. A collection of real data accumulated over years would be great.",
      },
    ],
  },
];

export function StatusPage() {
  const all = ITEMS.flatMap((g) => g.items);
  const counts = (state: State) => all.filter((i) => i.state === state).length;

  return (
    <div className="page">
      <section className="intro">
        <h1>Current State.</h1>
        <p>
          Everything that I can start on my own is here as a version 0.0.1. What
          we need now is a feedback loop that is as short as possible to begin
          actually refining the product. I am producing an adequacy number
          for the fake data in here, but it may not be precise or accurate.
          If a clinician or analyst looked at the data, reasoned about it and
          came up with their own adequacy ratio for a case, would it match what
          is presented here? I doubt it and that is what I want to work towards
          correcting.
        </p>
        <p>
          This is a full stack application that is running code intended for production. The python package
          that will be delivered to Mt. Sinai is the same package that is imported and used here. The case
          files (remittances, notes and EHR extracts) are the closest fascimily I could generate (thanks to Claude).
          However, the important detail there is that raw data files are being used and we can adapt this in any way
          we need to once we have feedback from Mt. Sinai.
        </p>
        <p>
          The UI you are looking at was built singularly to be a tool for the wider Seen team to begin to interface
          with the algorithm. The intention behind it is to be able to see the data being used, to see the methodology
          in action and to be as critical of it all as possible. The UI is irrelevant, if you have feeback that you think
          would make iterating more effective, I'm all ears, but when we actually send our product to Mt. Sinai, the UI you're using
          will not be included.
        </p>
        <p>
          In order to make this as easy as possible, you'll see a few tabs at the top of the page. 
        </p>
        <p>
          <strong>Complexity Score: </strong> This explains the specific methodology behind the complexity score. It is presented here
          so that the team can provide feedback and begin to make the adequacy ratio accurate.
        </p>
        <p>
          <strong>Data: </strong> This explains my fabricated data's shape. The intention is for the team to review it, download it, 
          modify it to be more meaningful, and then hand it back to me to use instead.
        </p>
        <p>
          <strong>Run: </strong> This is what Mt. Sinai will effectively do, just without this UI. Use this tool to generate the answers.
        </p>
        <p>
          <strong>Price: </strong> More of a novelty, almost didn't include it. This is what allows the 'complexity adjusted expected price'
          to be denominated in dollars.
        </p>
        <p>
          <strong>Downloads: </strong> Used to download the data for modification and return.
        </p>
      </section>

      {ITEMS.map((group) => (
        <section className="band" key={group.group}>
          <div className="eyebrow">{group.group}</div>
          {group.items.map((item) => (
            <div className="status-row" key={item.what}>
              <div className={`state ${item.state.replace(" ", "-")}`}>
                {LABEL[item.state]}
              </div>
              <div>
                <div className="status-what">{item.what}</div>
                <div className="status-detail">{item.detail}</div>
              </div>
              <div className="status-who">{item.who ?? ""}</div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
