import type { Metadata } from 'next';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Badge, Alert } from '@smartgym/ui';
import { PageHeader } from '@/components/page-header';
import { PipelineDiagnosticsClient } from './pipeline-diagnostics-client';

export const metadata: Metadata = { title: 'Pipeline Diagnostics' };
export const dynamic = 'force-dynamic';

export default async function DiagnosticsPage() {
  return (
    <>
      <PageHeader
        title="Edge AI Pipeline Diagnostics"
        description="Engineering verification tool for testing the 6-state finite state machine, joint angle mathematics, valgus detection, and false-positive rejection."
        breadcrumbs={[{ label: 'AI Diagnostics' }]}
      />

      <div className="mb-4">
        <Alert variant="warning" title="Developer & Lab Simulation Tool Only">
          This screen runs controlled synthetic and recorded pose keypoint trajectories to benchmark and calibrate the biomechanical algorithms.
          Production gym tracking feeds live camera frames through ONNX YOLOX + RTMPose into the edge-ai daemon.
        </Alert>
      </div>

      <PipelineDiagnosticsClient />
    </>
  );
}

