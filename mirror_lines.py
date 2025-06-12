def mirror_text_lines(text):
    """
    Mirror each line of the input text.
    """
    lines = text.splitlines()
    mirrored_lines = [line[::-1] for line in lines]
    return '\n'.join(mirrored_lines)

if __name__ == "__main__":
    input_text = "يكالهتسالا ليومتلا طباوضي"
    print("source: ", input_text)
    mirrored = mirror_text_lines(input_text)
    print("result: ", mirrored)