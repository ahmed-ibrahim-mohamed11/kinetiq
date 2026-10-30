import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { Alert } from '@smartgym/ui';

export const metadata: Metadata = { title: 'How your data is used' };

export default function PrivacyPage() {
  return (
    <article className="prose-sm max-w-none space-y-6">
      <Link href="/settings" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Settings
      </Link>
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">How your data is used</h1>
        <p className="text-sm text-muted-foreground">A plain-language description of what this system does with gym camera footage and your information.</p>
      </header>

      <Alert variant="info">
        This page explains how the product works. It is not legal advice and does not replace your gym&apos;s privacy notice or membership agreement.
      </Alert>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Checking in</h2>
        <p className="text-sm">
          You can check in by scanning the QR code from this app at the entrance, or by face identification if you have explicitly opted in. Face identification is optional; the QR code always works.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Face templates stay in the gym</h2>
        <p className="text-sm">
          If you opt in, a mathematical template of your face (not a photo) is stored encrypted on a computer inside the gym. It is used only by the entrance camera to recognise you. It is never uploaded to the cloud, never shared, and is deleted when you withdraw consent.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Workout tracking</h2>
        <p className="text-sm">
          With your consent, the gym&apos;s existing cameras are analysed live to recognise which machine you are using and to count sets and repetitions. Only the resulting events are kept: which equipment, when, how many sets and reps, and how confident the system was. No video is recorded or stored by this system.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">What your report contains</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Check-in and check-out times and total visit duration.</li>
          <li>Exercises performed, equipment used and time on each machine.</li>
          <li>Sets, repetitions per set and rest between sets.</li>
          <li>Periods where tracking was uncertain. These are always shown, never hidden.</li>
        </ul>
        <p className="text-sm">Weights are not detected or guessed; volume is expressed in repetitions only.</p>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Retention</h2>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Individual repetition events: 365 days.</li>
          <li>Workout reports and set summaries: kept while your membership is active.</li>
          <li>Raw video: never stored.</li>
          <li>Face templates: until you withdraw consent or your membership ends.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Your controls</h2>
        <p className="text-sm">
          You can review and withdraw each consent in <Link href="/settings" className="text-primary underline-offset-4 hover:underline">Settings</Link>. Only you and authorised gym staff for your branch can see your workout records; staff cannot see your face template.
        </p>
      </section>
    </article>
  );
}
