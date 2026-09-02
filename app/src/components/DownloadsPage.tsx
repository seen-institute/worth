import { archiveUrl, rawUrl } from "../api/client";
import { kb } from "../format";
import type { Dataset, FileSummary } from "../types";

/**
 * Where each delivered file lives in the dataset directory. The manifest
 * names files as the console shows them; the archive and the raw links use
 * the paths as they are on disk.
 */
const CLINICAL = "clinical";

function TableRow({ file }: { file: FileSummary & { kind: "table" } }) {
  return (
    <tr>
      <td>
        <a href={rawUrl(`${CLINICAL}/${file.name}`, true)} download={file.name}>
          {file.name}
        </a>
      </td>
      <td className="num">{file.rows}</td>
      <td className="num">{kb(file.bytes)}</td>
      <td className="sha">{file.sha256}</td>
    </tr>
  );
}

export function DownloadsPage({ dataset }: { dataset: Dataset }) {
  const tables = dataset.files.filter(
    (f): f is FileSummary & { kind: "table" } => f.kind === "table",
  );
  const count = (kind: "notes" | "edi"): number => {
    const f = dataset.files.find((x) => x.kind === kind);
    return f && f.kind !== "table" ? f.count : 0;
  };
  const notes = count("notes");
  const remits = count("edi");
  const total = dataset.files.reduce((sum, f) => sum + f.bytes, 0);

  return (
    <div className="page">
      <section className="intro">
        <h1>The files, as the pipeline reads them.</h1>
        <p>
          Everything on the Run tab is computed from the files below and nothing
          else. Take them away and read them from first principles: which
          fields the score is built from, what the operative notes say, what the
          remittance paid. Then change them. A different operative time, a
          harder note, a different allowed amount on an 835, a case moved
          between payers. Hand the changed files back and the pipeline can be
          run on them as a scenario, with the same arithmetic and the same
          receipts.
        </p>
        <p>
          Nothing here is real. The dataset is fabricated, and its assumptions
          are stated in the README that ships inside the archive and on the
          Data tab.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Everything, in one archive</div>
        <div className="download-lead">
          <a className="btn" href={archiveUrl()} download>
            Download the dataset (zip, {kb(total)} unpacked)
          </a>
        </div>
      </section>

      <section className="band">
        <div className="eyebrow">The clinical tables, one at a time</div>
        <p className="page-note">
          Pipe-delimited text with a header row. They open in a spreadsheet;
          keep the delimiter and the header when saving.
        </p>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>file</th>
                <th className="num">rows</th>
                <th className="num">size</th>
                <th>sha256, as served</th>
              </tr>
            </thead>
            <tbody>
              {tables.map((file) => (
                <TableRow key={file.name} file={file} />
              ))}
              <tr>
                <td>
                  <a href={rawUrl(`${CLINICAL}/notes.txt`, true)} download="notes.txt">
                    notes.txt
                  </a>
                </td>
                <td className="num">{notes}</td>
                <td className="num"></td>
                <td className="muted">the note index: one row per note file</td>
              </tr>
              <tr>
                <td>
                  <a href={rawUrl("README.md", true)} download="README.md">
                    README.md
                  </a>
                </td>
                <td className="num"></td>
                <td className="num"></td>
                <td className="muted">what the dataset is and what was put in</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="page-note">
          The {notes} notes and the {remits} remittance files are in the
          archive above, under <code>clinical/notes/</code>{" "}
          and <code>remittance/</code>. The Run tab shows every one of them
          as received.
        </p>
      </section>

      <section className="band">
        <div className="eyebrow">Changing them, and handing them back</div>
        <ol className="plain">
          <li>
            Unpack the archive and edit any file in place. Keep the file names
            and the directory layout; the pipeline reads them by name.
          </li>
          <li>
            The join between a case and its payment is the billing account
            number: <code>billing_account_id</code> on the operative log and{" "}
            <code>CLP01</code> on the 835. A case whose account number matches
            no remittance is reported as unlinked, and the linkage rate falls.
            That is a result, not an error.
          </li>
          <li>
            The allowed amount on a remittance line is <code>AMT*B6</code>.
            Change it and the numerator changes; nothing else in the 835 is
            read for the ratio.
          </li>
          <li>
            The score is read from the operative log's timestamps, ASA class,
            blood loss and team, the diagnosis list, and the sections of the
            operative note the rule pack names. The Complexity score tab lists
            every rule and the exact expressions it runs.
          </li>
          <li>
            Send the changed directory back. The server is pointed at a dataset
            directory by one setting, so a scenario is a copy of the directory
            and a restart, and its results carry that copy's hashes.
          </li>
        </ol>
      </section>
    </div>
  );
}
