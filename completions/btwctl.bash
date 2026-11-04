# bash completion for btwctl
_btwctl() {
    local cur prev cmd i
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD-1]}
    local commands="status info dump ll hq toggle hfp auto-hfp scan disconnect pair paired prefer game-mode forget monitor waybar gui tray raw"

    cmd=
    for ((i = 1; i < COMP_CWORD; i++)); do
        case ${COMP_WORDS[i]} in
            --device) ((i++)) ;;   # skip its value
            -*) ;;
            *) cmd=${COMP_WORDS[i]}; break ;;
        esac
    done

    if [[ -z $cmd ]]; then
        if [[ $prev == --device ]]; then return; fi
        COMPREPLY=($(compgen -W "$commands -v --verbose -h --help -V --version --notify --device" -- "$cur"))
        return
    fi
    case $cmd in
        status|info|dump) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        paired) COMPREPLY=($(compgen -W "--json --names" -- "$cur")) ;;
        scan) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        hfp|auto-hfp) [[ $prev == "$cmd" ]] && COMPREPLY=($(compgen -W "on off" -- "$cur")) ;;
        forget) COMPREPLY=($(compgen -W "-y --yes" -- "$cur")) ;;
        monitor) COMPREPLY=($(compgen -W "--notify --all --no-reconnect --no-automation" -- "$cur")) ;;
        game-mode) [[ $prev == "$cmd" ]] && COMPREPLY=($(compgen -W "status on off add remove" -- "$cur")) ;;
        waybar|tray) COMPREPLY=($(compgen -W "--interval" -- "$cur")) ;;
        pair|prefer)
            if [[ $cur == -* ]]; then
                if [[ $cmd == pair ]]; then
                    COMPREPLY=($(compgen -W "--timeout" -- "$cur"))
                else
                    COMPREPLY=($(compgen -W "--aptx --hfp --auto-hfp --current --clear --json" -- "$cur"))
                fi
            elif [[ $prev == --aptx ]]; then
                COMPREPLY=($(compgen -W "hq ll" -- "$cur"))
            elif [[ $prev == --hfp || $prev == --auto-hfp ]]; then
                COMPREPLY=($(compgen -W "on off" -- "$cur"))
            elif [[ $prev == "$cmd" ]]; then
                [[ $cmd == prefer && current == "$cur"* ]] && COMPREPLY+=(current)
                # case-insensitive, like 'pair' itself; names may contain spaces
                local name
                while IFS= read -r name; do
                    [[ ${name,,} == "${cur,,}"* ]] && COMPREPLY+=("$(printf %q "$name")")
                done < <(btwctl paired --names 2>/dev/null)
            fi ;;
    esac
}
complete -F _btwctl btwctl btw5-switch   # (btw5-switch: the name before 0.7)
