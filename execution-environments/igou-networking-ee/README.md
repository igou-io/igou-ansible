# igou-networking-ee

This EE is used to talk to legacy devices that can only use older KexAlgorithms/HostKeyAlgorithms/PubkeyAcceptedKeyTypes/Ciphers/etc

This approach is taken because ansible-navigator and awx/aap by extension ignore the variable `ssh_common_args`

If you still aren't able to use this image, consider building off rocky linux 8