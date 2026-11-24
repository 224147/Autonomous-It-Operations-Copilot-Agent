import { Link, Paper, Table, TableBody, TableCell, TableHead, TableRow } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

import { SeverityChip, StatusChip } from "../components/Chips";
import { EmptyState } from "../components/Common";
import type { Incident } from "../types";
import { humanize, timeAgo } from "../utils/format";

export function IncidentTable({ incidents }: { incidents: Incident[] }) {
  if (incidents.length === 0) return <EmptyState message="No incidents match." />;
  return (
    <Paper variant="outlined" sx={{ overflowX: "auto" }}>
      <Table size="small" aria-label="Incidents">
        <TableHead>
          <TableRow>
            <TableCell>ID</TableCell><TableCell>Severity</TableCell><TableCell>Title</TableCell><TableCell>Service</TableCell>
            <TableCell>Category</TableCell><TableCell>Status</TableCell><TableCell>Age</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {incidents.map((i) => (
            <TableRow key={i.id} hover>
              <TableCell><Link component={RouterLink} to={`/incidents/${i.id}`}>INC-{i.id}</Link></TableCell>
              <TableCell><SeverityChip severity={i.severity} /></TableCell>
              <TableCell sx={{ maxWidth: 420 }}>{i.title}</TableCell>
              <TableCell>{i.service}</TableCell>
              <TableCell>{humanize(i.category)}</TableCell>
              <TableCell><StatusChip status={i.status} /></TableCell>
              <TableCell>{timeAgo(i.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
