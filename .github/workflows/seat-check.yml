name: Seat check

on:
  schedule:
    - cron: "*/15 * * * *"   # every 15 minutes
  workflow_dispatch:
    inputs:
      test:
        description: "Send a test notification"
        type: boolean
        default: true

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install helper
        run: pip install aia
      - name: Check for an open seat
        run: python check_seat.py
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
          TEST_ALERT: ${{ inputs.test }}
