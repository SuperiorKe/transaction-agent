// Fixture data looks exactly like a live call, so every mock page says so and can't hide it
// (DESIGN.md decision 4; same rule as the labelled rehearsal recording).
export function MockStrip() {
  return (
    <div className="mock-strip" role="note">
      Mock data — not a live call
    </div>
  );
}
