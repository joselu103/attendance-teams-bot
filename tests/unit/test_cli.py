from attendance_teams_bot.cli import main


def test_command_reports_that_external_adapters_are_not_configured(capsys) -> None:
    main()

    assert capsys.readouterr().out == (
        "Attendance Teams Bot is ready for local composition testing; "
        "external adapters are not configured.\n"
    )
