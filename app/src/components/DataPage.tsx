import type { Dataset, FileSummary } from "../types";

function count(files: FileSummary[], kind: FileSummary["kind"]): number {
  const f = files.find((x) => x.kind === kind);
  if (!f) return 0;
  return f.kind === "table" ? f.rows : f.count;
}

export function DataPage({ dataset }: { dataset: Dataset }) {
  const notes = count(dataset.files, "notes");
  const remits = count(dataset.files, "edi");

  return (
    <div className="page">
      <section className="intro">
        <h1>What the fake data is.</h1>
        <p>
          The dataset is of course made up. What it is meant to show is is that,
          provided a given dataset x, the product will produce a given output y.
          Mt. Sinai can provide data of a completely, fundamentally different shape
          and it will be a trivial task to adapt the product to it. 
        </p>
        <p>
          The data is also 'happy path' right now. For an MVP I wanted the remittances
          and case files to link 1:1 with no orphans. I wanted typos in the notes to be
          non-existent, i.e. a clinician accidentally making a note that a surgery went 700 minutes
          instead of 70 like they intended.
        </p>
        <p>
          The ultimate intention is that Lori, J.D., John, Carly and the rest of the domain experts
          involved will be able to download the data, modify it to be more meaningful, and then hand
          it back to me to use instead. In which case I'll delete this page as it won't be relevant.
          Again, this is just to help everyone get oriented to what is happening.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">What is in the demo</div>
        <div className="scroll">
          <table>
            <tbody>
              <tr>
                <td>Surgeries</td>
                <td className="num">{dataset.encounters}</td>
                <td>
                  {dataset.study} study, {dataset.comparator} comparator
                </td>
              </tr>
              <tr>
                <td>Study codes</td>
                <td className="num">{dataset.codes.length}</td>
                <td>benign gynaecological surgery. {dataset.codes.join(", ")}</td>
              </tr>
              <tr>
                <td>Comparator codes</td>
                <td className="num">8</td>
                <td>general surgery, orthopaedics, urology</td>
              </tr>
              <tr>
                <td>Clinical notes</td>
                <td className="num">{notes}</td>
                <td>operative notes and discharge summaries</td>
              </tr>
              <tr>
                <td>Remittances</td>
                <td className="num">{remits}</td>
                <td>835 remittance files, one per payer per month</td>
              </tr>
              <tr>
                <td>Period</td>
                <td className="num">12 mo</td>
                <td>
                  {dataset.periodStart} to {dataset.periodEnd}, one hospital
                </td>
              </tr>
              <tr>
                <td>Payers</td>
                <td className="num">4</td>
                <td>blinded in every computed output</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="band">
        <div className="eyebrow">The three types of data</div>
        <p className="page-note">
          I believe this matches the brief. The packaging is my best interpretation. Easy to change.
        </p>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>stream</th>
                <th>As it is presented here</th>
                <th>where it comes from</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Structured clinical</td>
                <td>five delimited tables</td>
                <td>Epic Clarity, direct</td>
              </tr>
              <tr>
                <td>Unstructured notes</td>
                <td>an index plus one file per note</td>
                <td>Epic Clarity or Caboodle, direct, native free text</td>
              </tr>
              <tr>
                <td>Remittance</td>
                <td>raw ANSI 835 remittance</td>
                <td>Epic Resolute or the clearinghouse feed</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="page-note">
          Patient identifiers are a limited data set: pseudonymous id, birth
          month, ZIP, county. Service dates and geography are kept because
          episode timing and market slicing depend on them.
        </p>
      </section>
    </div>
  );
}
