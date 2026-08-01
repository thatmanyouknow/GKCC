{
  "version": 1,
  "source": "https://github.com/moggieuk/Happy-Hare/tree/main/config/addons",
  "title": "Blobifier installation, configuration, and first-run path",
  "steps": [
    {
      "id": "blob_hardware_inventory",
      "title": "Confirm the installed hardware",
      "summary": "Record printer size, Blobifier location, tray/base version, servo type, bucket switch, shaker, and whether the existing brush will be reused. Photograph or note anything that differs from the standard rear-left Voron V2 arrangement.",
      "kind": "confirm"
    },
    {
      "id": "blob_power_off_wiring",
      "title": "Verify wiring with printer power removed",
      "summary": "Confirm 5 V and ground, then enter the exact Klipper servo output pin and bucket-switch input pin in GKCC. Record the controller and physical connector, verify pull-up/inversion syntax, strain relief, and clearance from mains wiring before powering the printer.",
      "kind": "confirm"
    },
    {
      "id": "blob_import_official",
      "title": "Load the Happy Hare Blobifier files",
      "summary": "Use the Blobifier files shipped with the installed Happy Hare version. Rebase the live configuration so GKCC imports mmu/addons/blobifier.cfg and blobifier_hw.cfg when they are present.",
      "kind": "config"
    },
    {
      "id": "blob_include_review",
      "title": "Review includes and Happy Hare integration",
      "summary": "Verify [include mmu/addons/blobifier.cfg], set purge_macro to BLOBIFIER, review force_purge_standalone and wipe-tower settings, and use the current Happy Hare parking configuration rather than an obsolete post-load hook.",
      "kind": "config"
    },
    {
      "id": "blob_switch_test",
      "title": "Test the bucket switch",
      "summary": "With no machine movement, press and release the switch by hand, then with the bucket installed. Confirm the live gcode_button state and the installed/removed messages. Removing the bucket resets the saved blob count in the official hardware file.",
      "kind": "verification"
    },
    {
      "id": "blob_servo_out",
      "title": "Set and test tray-out travel",
      "summary": "Keep fingers clear, command BLOBIFIER_SERVO POS=out, and adjust the out angle or minimum pulse width until the tray fully extends without buzzing or binding.",
      "kind": "calibration"
    },
    {
      "id": "blob_servo_in",
      "title": "Set and test tray-in travel",
      "summary": "Command BLOBIFIER_SERVO POS=in and adjust the in angle or maximum pulse width until the tray fully retracts without buzzing or forcing the linkage.",
      "kind": "calibration"
    },
    {
      "id": "blob_manual_slide",
      "title": "Verify the mechanical slide and collision envelope",
      "summary": "Power the servo down between tests if necessary. Confirm the tray slides freely, the linkage screw is not too tight, and the bed, toolhead, cutter pin, probe, and wiring cannot collide through the full travel.",
      "kind": "confirm"
    },
    {
      "id": "blob_measure_geometry",
      "title": "Measure tray and brush geometry",
      "summary": "Home and level the gantry, then approach from a deliberately high Z. Record purge X, tray top, brush start, brush width, brush top, brush Y offset, toolhead extents, and safe clearances. Reusing the old brush is acceptable when the nozzle contacts the bristles correctly and all travel remains in range.",
      "kind": "calibration"
    },
    {
      "id": "blob_dry_clean",
      "title": "Run a dry brush test",
      "summary": "With a cold, empty nozzle and clear bed, run BLOBIFIER_CLEAN while standing at the printer. Stop immediately for an out-of-range move, bed contact, probe contact, or excessive brush depth.",
      "kind": "verification"
    },
    {
      "id": "blob_dry_shake",
      "title": "Verify bucket-shaker motion",
      "summary": "When a shaker is installed, test a small number of shakes only after confirming the toolhead slot height and X/Y path. Disable the shaker until this passes.",
      "kind": "verification"
    },
    {
      "id": "blob_hot_small",
      "title": "Create the first supervised test blob",
      "summary": "Load suitable filament, confirm the bucket and tray are installed, heat safely, and run a small explicit purge length. Observe tray extension, purge placement, Z rise, blob release, tray retraction, and brushing. Keep emergency stop ready.",
      "kind": "verification"
    },
    {
      "id": "blob_tune_shape",
      "title": "Tune blob formation one variable at a time",
      "summary": "Adjust purge start, purge speed, Z raise, Z exponent, purge maximum, pressure-release time, fan settings, and between-blob retraction. Record each result instead of changing several values together.",
      "kind": "calibration"
    },
    {
      "id": "blob_purge_matrix",
      "title": "Validate purge-volume input",
      "summary": "Confirm Happy Hare receives slicer purge volumes or use an explicit PURGE_LENGTH for testing. Tune the purge-length modifier and addition only after the physical blob sequence is reliable.",
      "kind": "verification"
    },
    {
      "id": "blob_repeat",
      "title": "Run repeated supervised cycles",
      "summary": "Complete at least ten representative purge, eject, brush, and optional shake cycles. Record failures, blob count, bucket-switch operation, and any servo buzzing or missed blobs.",
      "kind": "verification"
    },
    {
      "id": "blob_live_apply",
      "title": "Review, back up, and apply the final files",
      "summary": "Preview the exact diff, create a backup of every changed file, apply live, restart Klipper, verify ready state, and automatically roll back if the configuration is rejected.",
      "kind": "config"
    },
    {
      "id": "blob_toolchange",
      "title": "Verify a real Happy Hare tool change",
      "summary": "Disable the slicer wipe tower only after a supervised tool change successfully calls Blobifier, purges the expected amount, cleans the nozzle, and returns without contaminating the print area.",
      "kind": "verification"
    }
  ]
}
