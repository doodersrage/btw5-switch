# fish completion for btw5-switch
set -l cmds status info dump ll hq toggle hfp auto-hfp scan disconnect pair paired forget monitor waybar tray raw
complete -c btw5-switch -f
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -s v -l verbose -d 'hex-dump USB traffic'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -s V -l version -d 'show version'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a status -d 'show headset and settings'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a info -d 'firmware version and serial'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a dump -d 'every readable register'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a ll -d 'aptX Adaptive Low Latency'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a hq -d 'aptX Adaptive High Quality'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a toggle -d 'switch between LL and HQ'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a hfp -d 'headset microphone on/off'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a auto-hfp -d 'automatic HFP switching on/off'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a scan -d 'list nearby headsets'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a disconnect -d 'disconnect the headset'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a pair -d 'connect to a headset'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a paired -d 'list remembered headsets'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a forget -d 'forget all paired headsets'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a monitor -d 'watch for changes'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a waybar -d 'status bar JSON'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a tray -d 'system tray icon'
complete -c btw5-switch -n "not __fish_seen_subcommand_from $cmds" -a raw -d 'send a raw command'

complete -c btw5-switch -n "__fish_seen_subcommand_from status info dump scan paired" -l json -d 'machine-readable output'
complete -c btw5-switch -n "__fish_seen_subcommand_from paired" -l names -d 'names only'
complete -c btw5-switch -n "__fish_seen_subcommand_from hfp auto-hfp" -a 'on off'
complete -c btw5-switch -n "__fish_seen_subcommand_from pair" -a '(btw5-switch paired --names 2>/dev/null)' -d 'paired headset'
complete -c btw5-switch -n "__fish_seen_subcommand_from pair" -l timeout -r
complete -c btw5-switch -n "__fish_seen_subcommand_from forget" -s y -l yes -d "don't ask"
complete -c btw5-switch -n "__fish_seen_subcommand_from monitor" -l notify -d 'desktop notifications'
complete -c btw5-switch -n "__fish_seen_subcommand_from monitor" -l all -d 'print every report'
complete -c btw5-switch -n "__fish_seen_subcommand_from monitor" -l no-reconnect -d 'exit when the dongle disappears'
complete -c btw5-switch -n "__fish_seen_subcommand_from waybar tray" -l interval -r
complete -c btw5-switch -n "__fish_seen_subcommand_from raw" -l listen -r
