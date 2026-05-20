#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <arpa/inet.h>
#include <linux/input.h>

int main(int argc, char *argv[]) {
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    if (argc < 2) {
        fprintf(stderr, "Usage: %s /dev/input/eventX [server_ip] [server_port]\n", argv[0]);
        return 1;
    }

    const char *device = argv[1];
    const char *server_ip = (argc >= 3) ? argv[2] : "192.168.1.6";
    int server_port = (argc >= 4) ? atoi(argv[3]) : 5005;

    int fd = open(device, O_RDONLY);
    if (fd < 0) {
        perror("open input device failed");
        return 1;
    }

    int sock = socket(AF_INET, SOCK_DGRAM, 0);

    struct sockaddr_in addr;
    addr.sin_family = AF_INET;
    addr.sin_port = htons(server_port);
    addr.sin_addr.s_addr = inet_addr(server_ip);

    struct input_event ev;

    fprintf(stderr, "Streaming from %s to %s:%d...\n", device, server_ip, server_port);

    while (1) {
        ssize_t n = read(fd, &ev, sizeof(ev));
        if (n != sizeof(ev)) continue;

        sendto(sock, &ev, sizeof(ev), 0,
               (struct sockaddr*)&addr, sizeof(addr));
    }

    close(fd);
    close(sock);
    return 0;
}