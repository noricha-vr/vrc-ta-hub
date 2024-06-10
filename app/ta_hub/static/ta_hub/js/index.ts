const patterns: string[] = [
    "さまざまなバックグラウンドを持つ人々と交流することで、視野が広がった！！とんがった知識でもバッチコイ！",
    "VRChatの技術学術系集会は、困ったときに頼れるすごい人たちがいるので、とても助かります。みんな親切で、相談しやすい雰囲気が大好きです。",
    "リアルでは滅多に会えないようなすごい人とお話ができてお得です。"
];

const petiElement: HTMLElement | null = document.getElementById("petipeti");
const randomIndex: number = Math.floor(Math.random() * patterns.length);

if (petiElement) {
    petiElement.textContent = patterns[randomIndex];
}