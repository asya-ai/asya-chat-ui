const PASSWORD_REGEX = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{10,}$/

export const isValidPassword = (value: string) => PASSWORD_REGEX.test(value)
