# bash completion for btw5-switch
_btw5_switch() {
    local cur prev cmd i
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD-1]}
    local commands="status info dump ll hq toggle hfp auto-hfp scan disconnect pair paired forget monitor waybar gui tray raw"

    cmd=
    for ((i = 1; i < COMP_CWORD; i++)); do
        case ${COMP_WORDS[i]} in -*) ;; *) cmd=${COMP_WORDS[i]}; break ;; esac
    done

    if [[ -z $cmd ]]; then
        COMPREPLY=($(compgen -W "$commands -v --verbose -h --help -V --version" -- "$cur"))
        return
    fi
    case $cmd in
        status|info|dump) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        paired) COMPREPLY=($(compgen -W "--json --names" -- "$cur")) ;;
        scan) COMPREPLY=($(compgen -W "--json" -- "$cur")) ;;
        hfp|auto-hfp) [[ $prev == "$cmd" ]] && COMPREPLY=($(compgen -W "on off" -- "$cur")) ;;
        forget) COMPREPLY=($(compgen -W "-y --yes" -- "$cur")) ;;
        monitor) COMPREPLY=($(compgen -W "--notify --all --no-reconnect" -- "$cur")) ;;
        waybar|tray) COMPREPLY=($(compgen -W "--interval" -- "$cur")) ;;
        pair)
            if [[ $cur == -* ]]; then
                COMPREPLY=($(compgen -W "--timeout" -- "$cur"))
            elif [[ $prev == "$cmd" ]]; then
                # case-insensitive, like 'pair' itself; names may contain spaces
                local name
                while IFS= read -r name; do
                    [[ ${name,,} == "${cur,,}"* ]] && COMPREPLY+=("$(printf %q "$name")")
                done < <(btw5-switch paired --names 2>/dev/null)
            fi ;;
    esac
}
complete -F _btw5_switch btw5-switch
